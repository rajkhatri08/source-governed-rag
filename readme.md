# Source-Governed RAG

A retrieval-augmented generation system for regulatory compliance documents, where **document governance overrides retrieval confidence**.

Most RAG systems answer from whatever text is most semantically similar to the question. This one checks whether that text should be trusted at all — whether the source document is current, and whether anyone approved it — before deciding how much weight to give the answer.

**Live demo:** https://compliance-dashboard-omega-amber.vercel.app
**API:** https://source-governed-rag.onrender.com/docs

> The backend runs on a free tier that sleeps after 15 minutes of inactivity. The first request may take 30–50 seconds.

---

## The problem

Semantic similarity has no notion of recency or authority.

The system indexes three documents: GDPR Chapter 3, an internal data retention policy whose review date passed in January 2023, and an unapproved draft AI usage policy. All three say something about breach notification deadlines, and they disagree:

| Source | Deadline | Governance status |
|---|---|---|
| GDPR Art. 33 | 72 hours | Approved, current |
| Data Retention Policy v1.0 | 30 days | Approved, **expired 2023** |
| Draft AI Usage Policy v0.3 | 24 hours | **Never approved** |

Asked "how long do we have to report a data breach", vector search ranks the **expired policy first** — distance 0.743, against 0.894 for the current GDPR article. An ungoverned system would present its 30-day figure as the closest match, which would put an organisation in breach of the regulation it was trying to comply with.

The governance layer catches this. The answer is returned, the conflict is surfaced, the expired source is named, and the response is downgraded to SILVER.

---

## Architecture

```
Browser (Next.js / Vercel)
    |
    v
FastAPI (Render)
    |
    +--> ChromaDB          in-memory vector store, all-MiniLM-L6-v2 via ONNX
    +--> Gemini 3.6 Flash  grounded generation
    +--> PostgreSQL (Neon) document metadata + audit log
```

### Request flow

1. Question validated (400 on empty input)
2. Retrieve top 5 chunks from ChromaDB
3. **Filter to chunks below distance 1.2** — one filter, applied before both generation and governance
4. Build warnings from the surviving chunks' metadata
5. Generate an answer from those chunks only, with an instruction to refuse if the context does not contain it
6. Classify GOLD / SILVER / BRONZE
7. Write an audit row to Postgres
8. Return answer, tier, reason, warnings, sources and distances

### Modules

| File | Responsibility |
|---|---|
| `ingest.py` | Load text and PDF documents, clean, chunk |
| `retrieve.py` | Build the vector index, search, add to a live index |
| `generate.py` | Prompt construction and the model call |
| `govern.py` | Expiry check and tier classification |
| `db.py` | SQLAlchemy models, audit log, document metadata |
| `api.py` | FastAPI endpoints |
| `evaluate.py` | 23-question evaluation harness with result caching |
| `calibrate.py` | Threshold calibration against the labelled set |
| `migrate.py` | One-off migration of document metadata into Postgres |

---

## Chunking: measured, not assumed

Chunk strategy was chosen by measurement, not by convention. Each configuration was run against the same labelled evaluation set.

| Strategy | Accuracy | False refusals | False answers |
|---|---|---|---|
| 300 chars, 50 overlap | 78% (14/18) | 3 | 1 |
| 1000 chars, 50 overlap | 90% (18/20) | 2 | 0 |
| **Article boundaries** | **95% (19/20)** | **1** | **0** |

**Why character chunking failed.** At 300 characters, Article 15's enumerated list of data subject rights was cut mid-word — the chunk contained the heading and opening sentence but stopped before subsection (c), which was the answer to the question being asked. The model refused, correctly, because it genuinely had not received the answer. The same failure recurred at 1000 characters one level deeper, in Article 17(3).

Splitting on article boundaries keeps each legal provision intact:

```python
re.split(r"(?=Art\. \d+ GDPR)", document)
```

The lookahead matches the position before each heading without consuming it, so every chunk retains its own title.

---

## Governance is a veto, not a weight

The tier is decided by three checks, in order:

```python
if model_refused:            return BRONZE, "no answer found in the available sources"
if any_source_unapproved:    return BRONZE, "source not approved: ..."
if any_source_expired:       return SILVER, "source past review date: ..."
                             return GOLD,   "answered from an approved, current source"
```

**Order matters.** If governance were combined into a weighted score, a very close semantic match could outweigh the fact that nobody approved the document. Checking approval first and returning immediately makes that impossible.

The concrete case: the question *"how quickly must AI-related data requests be answered"* retrieves the draft AI policy at **distance 0.720 — the best match in the entire evaluation set**. It is still classified BRONZE, because the document was never approved.

**Expired downgrades to SILVER, unapproved to BRONZE.** This is a deliberate distinction. An expired document was approved once and may still be broadly correct; it needs review, not rejection. An unapproved draft never carried authority at all.

---

## Document upload

Documents can be uploaded at runtime through the dashboard or the API, as plain text or PDF. An uploaded document is chunked, added to the live ChromaDB collection, and searchable immediately — no restart required to retrieve from it.

**Uploaded documents default to unapproved.** This is the governance premise applied to ingestion: a new document is retrievable but cannot produce a GOLD answer until someone explicitly approves it. Answers drawn from it are returned with a named warning and a BRONZE tier.

PDF text is extracted with `pypdf` and passed through a PDF-specific cleaning stage before the general one. That stage normalises ligatures (`ﬁ` → `fi`, `ﬂ` → `fl`), rejoins lines broken by the PDF's visual layout rather than by sentence structure, and collapses the resulting double spaces.

The endpoint validates the review date and claims the filename in the database **before** writing anything to disk, so a rejected upload leaves no stray file and does no extraction work.

---

## Retirement, not deletion

A document can be retired, which removes it from the index so it can no longer answer questions, while leaving the row and the file in place.

Hard-deleting would break the audit trail. Entries in `audit_log` name their sources; delete the document and past entries reference something that no longer exists, with no way to know what it said. For a system whose premise is defensibility, destroying the record is the wrong default.

Retirement takes effect on restart, and is currently one-way — there is no reinstate endpoint.

---

## Why distance thresholds were removed

The tier originally included similarity thresholds — above 1.1 BRONZE, above 0.95 SILVER. Reviewing the audit log showed misclassifications in both directions, so the thresholds were calibrated against the labelled set rather than defended by intuition.

`calibrate.py` tests every threshold from 0.80 to 1.50 in steps of 0.01 and reports the best:

```
best threshold: 1.02
correct: 17 of 23
accuracy: 73.9 %
```

**73.9% is the ceiling, not the current setting.** No threshold separates answerable from unanswerable questions better than that, because the two distributions overlap: answerable questions ranged 0.868–1.368, unanswerable 1.057–1.644. Two questions about DPO appointment and child consent — neither answerable from this corpus — scored better than several questions that were, because Article 12 mentions both topics in passing without answering them.

Similarity measures topical relatedness, not whether the answer is present.

The thresholds were removed. The tier now rests only on the model's own refusal signal and on document governance status, both of which are defensible. A relevance cutoff of 1.2 remains, but it decides which chunks are considered, not what the answer is worth.

---

## Evaluation

`eval_questions.py` holds 23 labelled questions in three groups:

- **10 answerable** — the answer exists in Articles 12, 15, 17 or 33
- **10 out of corpus** — real GDPR, but in articles that were not indexed (fines, DPOs, international transfers, DPIAs)
- **3 conflicted** — multiple sources disagree, or the only source is unapproved

The out-of-corpus questions are deliberately plausible. Testing with obviously unrelated questions proves nothing; the useful cases are the ones that sound like they belong to the document but are not in it.

`evaluate.py` caches results keyed on question plus chunk count, so re-running an unchanged configuration costs no API calls while changing the chunking strategy correctly invalidates the cache.

---

## Running locally

```bash
git clone https://github.com/rajkhatri08/source-governed-rag
cd source-governed-rag
pip install -r requirements.txt
```

Create `.env`:

```
GOOGLE_API_KEY=your_gemini_key
DATABASE_URL=postgresql://user:password@localhost:5432/compliance
```

Initialise the database and load document metadata:

```bash
python -c "from db import init_db; init_db()"
python migrate.py
```

Run the API:

```bash
uvicorn api:app --reload
```

Interactive docs at `http://127.0.0.1:8000/docs`.

The frontend is a separate repository: [compliance-dashboard](https://github.com/rajkhatri08/compliance-dashboard).

---

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness check |
| POST | `/query` | Ask a question; returns answer, tier, reason, warnings, sources |
| GET | `/documents` | Document inventory with computed expiry status |
| POST | `/upload` | Upload a `.txt` or `.pdf`; indexed immediately, unapproved by default |
| POST | `/documents/{filename}/approve` | Approve a document |
| POST | `/documents/{filename}/unapprove` | Revoke approval |
| POST | `/documents/{filename}/retire` | Retire a document: removed from the index, kept in the audit trail |
| GET | `/audit` | Full audit log, newest first |

Errors: `400` on an empty question or a malformed review date, `404` on an unknown document, `409` on a duplicate filename, `503` when the model is unavailable.

---

## Known limitations

These are real and worth stating plainly.

**Governance changes require a restart to affect retrieval.** Uploaded documents are indexed immediately and searchable straight away. But approving, revoking or retiring a document writes to Postgres without updating the chunk metadata already held in the index, so the change does not affect answers until the service restarts. Making it immediate would need chunk ids tracked per document so the affected entries could be updated or deleted in place.

**Retirement is one-way.** There is no reinstate endpoint. A retired document can only be brought back by editing the database directly.

**The document row is created before the file is written.** If the write failed, the database would hold a row pointing at a missing file. Doing this properly needs a transaction spanning Postgres and the filesystem, which is awkward when only one of them supports transactions.

**PDF cleaning is heuristic.** The line-joining regex uses punctuation and capitalisation to guess where a sentence continues across a line break. It works on the documents tested, but it is a guess, not a rule, and it will misjudge some layouts.

**The 1.2 relevance cutoff was chosen by inspection**, not calibration. Unlike the tier thresholds, it has not been measured, and it is a known weak point. Short, casually phrased questions produce weaker embedding matches than formally worded ones and can fall outside it even when the corpus does answer them.

**The evaluation set is 23 questions**, all written by one person. Large enough to compare chunking strategies against each other; too small to claim an absolute accuracy figure with confidence.

**No unit tests.** The evaluation harness tests retrieval quality end to end, but nothing checks that `chunk_by_article` handles a document with no headings, or that `is_expired` handles a malformed date.

**The chunker is GDPR-specific.** It matches `Art. N GDPR` headings. A document without them becomes a single chunk. A general system needs structure detection per document type with a character-based fallback.

**Chunk ids are assigned from a counter held in process memory.** It survives as long as the process does, but a restart rebuilds the index from scratch, and concurrent uploads could collide. UUIDs would remove the coordination entirely.

**Schema changes are applied by hand.** The `retired` column was added with a manual `ALTER TABLE` against each database. A migration tool such as Alembic is what this needs before there is real data to protect.

**Cold starts.** Render's free tier sleeps after 15 minutes; the first request afterwards takes 30–50 seconds.

---

## Design decisions worth explaining

**Retrieval built directly rather than with LangChain.** A splitter is a few lines of configuration that hide chunk boundaries, overlap behaviour and edge cases — precisely the things that determined the 78%-to-95% improvement. Building it manually meant those failures were visible and diagnosable.

**ChromaDB's built-in embedding function rather than sentence-transformers.** The original implementation loaded `all-MiniLM-L6-v2` through PyTorch. `pip freeze` captured the CUDA-enabled build, which pulled roughly 2 GB of GPU libraries onto a 512 MB instance and killed the deploy with SIGKILL. Chroma ships the same model through ONNX at 79 MB. Same embeddings, same results, deployable.

**Expiry computed at request time, not stored.** A stored flag goes stale the day it should flip.

**Configuration through environment variables.** `os.getenv("DATABASE_URL", fallback)` means the same code runs locally against Postgres on `localhost` and in production against Neon, with no changes.

---

## Stack

Python 3.14 · FastAPI · SQLAlchemy · ChromaDB · pypdf · Google Gemini · PostgreSQL
Next.js 16 · React · Tailwind · Recharts
Render · Vercel · Neon
