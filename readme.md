# Source-Governed RAG

[![CI](https://github.com/rajkhatri08/source-governed-rag/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/rajkhatri08/source-governed-rag/actions/workflows/ci.yml)

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

Asked "how long do we have to report a data breach", vector search ranks the **expired policy first** — distance 0.673 for its breach-notification section, against 0.837 for the paragraph of the current GDPR article that sets the 72-hour deadline. These distances come from the structure-aware chunker, which splits each document into its sections and paragraphs (see [Chunking](#chunking-measured-not-assumed)). An ungoverned system would present its 30-day figure as the closest match, which would put an organisation in breach of the regulation it was trying to comply with.

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
    +--> PostgreSQL (Neon) document text and metadata + audit log
```

### Request flow

1. Question validated (400 on empty input)
2. Retrieve top 5 chunks from ChromaDB
3. **Filter to chunks below distance 1.2** — one filter, applied before both generation and governance
4. Build warnings from the surviving chunks' metadata: one per source and problem, in the order retrieved
5. Generate an answer from those chunks only. The prompt tells the model to reply `NO_ANSWER_IN_CONTEXT` if the context does not contain the answer; a reply holding that token anywhere, or holding no text, is a refusal, and the user sees a fixed sentence instead
6. Classify GOLD / SILVER / BRONZE
7. Write an audit row to Postgres: the question, the answer, tier, reason and warnings, and every chunk the answer was drawn from, with its id, source document, content hash and distance
8. Return answer, tier, reason, whether it was a refusal, warnings, sources, distances, and how many milliseconds retrieval, generation and the audit write took

A chunk's id is `{filename}::{position}::{first 12 characters of the SHA-256 of its text}`. It comes from the chunk itself, so the same text gets the same id after every restart, and the hash in an audit row shows exactly which text an answer used.

### Modules

| File | Responsibility |
|---|---|
| `ingest.py` | Clean and chunk text; rejoin hyphenated words |
| `upload.py` | Every check on an upload, before anything is stored; text extraction from .txt and .pdf, dropping page furniture |
| `retrieve.py` | The one embedding model; build the vector index, embed, search, add to a live index; content-derived chunk ids |
| `generate.py` | Prompt construction, the Gemini client (with a timeout) and the model call |
| `govern.py` | The refusal check, approval and review-date checks (fail closed), warnings and tier classification |
| `db.py` | SQLAlchemy models, audit log, document metadata |
| `api.py` | FastAPI endpoints |
| `pipeline.py` | The question-answering pipeline, shared by the API and the evaluation |
| `evals/` | Evaluation v2: 48 pre-registered questions, the harness and its scoring; the PDF test set and its measurement |
| `calibrate.py` | Threshold calibration against the labelled set |
| `migrations/`, `alembic.ini` | Alembic schema migrations; `migrations/baseline_schema.txt` is the schema revision 0001 creates |
| `print_schema.py` | Read-only schema print, to compare a database with the baseline before stamping it |
| `migrate.py` | One-off load of document metadata into Postgres |
| `backfill_content.py` | Fills the stored text of documents created before it was kept, from the seed files in `documents/` |
| `backfill_vectors.py` | Stores the vectors of existing documents' chunks, so startup doesn't embed them again |
| `tests/`, `testdb.py` | The pytest suite; `testdb.py` keeps the tests on the `compliance_test` database and off any other |

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

**Since P0.5, chunks follow each document's own structure** (`ingest.chunk_document`). An article longer than 1,500 characters — every article in the corpus — is split at its numbered paragraphs, and every piece is prefixed with the article's heading and title, so it still says where it comes from: `Art. 12 GDPR | Transparent information, … | 3. 1The controller shall provide …`. An internal policy is split at its `Section N.` headings, each piece prefixed with the policy's title and version. A document with neither is packed line by line into chunks of up to 1,000 characters. A long chunk's embedding averages many topics: whole, Art. 12 passed the 1.2 filter for none of the 17 evaluation questions that need it; split, it passes for 10. On evaluation v2 the change moved correct refusals from 36 to 41 of 48, answers with the expected facts from 17 to 26 of 34, and correct tiers from 28 to 31 of 48, with five questions getting worse — each explained in [docs/eval/p0.5.md](docs/eval/p0.5.md). The table above comes from the earlier harness and the article chunker; P3.2 re-runs that comparison on evaluation v2.

---

## Governance is a veto, not a weight

The tier is decided by these checks, in order:

```python
if model_refused:             return BRONZE, "no answer found in the available sources"
if no_source_under_cutoff:    return BRONZE, "no relevant source in the corpus"
if any_source_unapproved:     return BRONZE, "source not approved: ..."
if any_source_past_review:    return SILVER, "source past review date: ..."   # or "has no valid review date"
                              return GOLD,   "answered from an approved, current source"
```

When more than one source is past review, or has no valid review date, the reason names the first of them in retrieval order; the warnings list every one.

**Unknown counts against the source.** Approved means exactly `True`: `False`, a missing value or `None` all count as unapproved. A missing or unreadable review date counts as past review, and the reason says so. A model refusal is recognised by a token the prompt asks for (`NO_ANSWER_IN_CONTEXT`), not by phrases like "I don't know", which also appear in real answers.

**Order matters.** If governance were combined into a weighted score, a very close semantic match could outweigh the fact that nobody approved the document. Checking approval first and returning immediately makes that impossible.

The concrete case: the question *"how quickly must AI-related data requests be answered"* retrieves the draft AI policy at **distance 0.720 — the best match in the entire evaluation set**. It is still classified BRONZE, because the document was never approved.

**Expired downgrades to SILVER, unapproved to BRONZE.** This is a deliberate distinction. An expired document was approved once and may still be broadly correct; it needs review, not rejection. An unapproved draft never carried authority at all.

---

## Document upload

Documents can be uploaded at runtime through the dashboard or the API, as plain text or PDF. An uploaded document's text, and its chunks with their vectors, are stored in Postgres, then added to the live ChromaDB collection, so it is searchable immediately and still there after a restart. At startup the index is rebuilt from the vectors stored in Postgres, so a cold start doesn't run the embedding model over every chunk again. Nothing is written to disk; `documents/` holds only the seed documents.

**Uploaded documents default to unapproved.** This is the governance premise applied to ingestion: a new document is retrievable but cannot produce a GOLD answer until someone explicitly approves it. Answers drawn from it are returned with a named warning and a BRONZE tier.

PDF text is extracted with `pypdf` and cleaned in stages:
- **Page furniture is dropped.** A line at the top or bottom of a page is a header, footer or page number if, with its digits ignored, it repeats there on at least half of all pages, or of the odd or the even pages: printed documents often alternate them.
- **Pages are joined so that a sentence continuing onto the next page joins up.**
- **A word hyphenated at a line end is rejoined.**
- **The PDF-specific stage** normalises ligatures (`ﬀ ﬁ ﬂ ﬃ ﬄ` → `ff fi fl ffi ffl`), rejoins lines broken by the PDF's visual layout rather than by sentence structure, and collapses the resulting double spaces.

[docs/eval/pdf_robustness.md](docs/eval/pdf_robustness.md) measures each stage on seven real PDFs.

**Every check runs before anything is stored** (`upload.py`), cheapest first. A failed upload therefore leaves nothing behind.
- **The name** must be a plain file name:
  - letters and digits in any language, spaces, and `. _ - ( ) & , ' +`, plus the typographic apostrophes and dashes `’ ‘ – —` that Word and macOS insert
  - no `/` `\` `:` `#` `?` `%` or control characters
  - not starting with a dot, at most 200 characters, ending in `.pdf` or `.txt` (any case)

  It is normalised to NFC, so an accented letter is stored one way, however the browser sent it.
- **The owner, version and review date** must fit their columns, and the date must be a real `YYYY-MM-DD`.
- **The file** must be at most 10 MB, and a PDF at most 300 pages (counted before any text is extracted).
- **There must be text to extract.** A PDF with no text layer (a scan), a password-protected PDF, a PDF that can't be read, or a .txt that isn't UTF-8 is refused with a message saying which.
- **The text must fit the chunk limit:** 300 chunks (about 300,000 characters) unless `MAX_UPLOAD_CHUNKS` says otherwise. The live server sets 100 chunks (about 100,000 characters): embedding is the slow part of an upload, and there an upload takes about 3 seconds per chunk.

A name that is already taken is refused next, before minutes of embedding are spent on it. Only then are the chunks embedded, and only then are the row, its text and its chunks' vectors inserted, in one transaction. That early name check doesn't decide: Postgres's unique constraint does, so two simultaneous uploads of one name can't both succeed. If indexing then fails, the row is removed again and the upload reports that nothing was saved. One upload is processed at a time; another arriving meanwhile is asked to try again in a few minutes.

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

**Evaluation v2** (`evals/`) keeps these 23 questions verbatim and adds 25 more (casual paraphrases, one question per governance state, conflicting deadlines and harder out-of-corpus cases), all labelled before any run and then frozen. It calls the same function as `/query`, against a dedicated database seeded to a fixed governance state with a fixed date, and scores refusals, expected facts and tiers automatically, each over a fixed denominator. The first run ([docs/eval/baseline.md](docs/eval/baseline.md)) refuses correctly on 36 of 48 questions, includes the expected facts in 17 of 34 answers, and gives the expected tier on 28 of 48. These are lower than the chunking results above, which came from an earlier harness that sent the top 3 chunks to the model with no relevance filter and was scored by hand. Every miss traces to retrieval: either the chunk with the answer did not pass the 1.2 cutoff, or a whole-document policy chunk passed it, or failed to, and changed the tier.

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

Create the tables with the migrations, load document metadata, then store each seed document's text:

```bash
alembic upgrade head
python migrate.py
python backfill_content.py
python backfill_vectors.py
```

`backfill_vectors.py` stores each chunk's vector, so the API starts without re-embedding; without it, startup still works, embedding the documents again. `MAX_UPLOAD_CHUNKS` in `.env` raises the upload chunk limit (300 by default), for example to try a long regulation locally.

Every schema change is an Alembic migration in `migrations/versions/`. `alembic upgrade head` applies the ones a database hasn't had yet; `alembic upgrade 0001:head --sql` prints their SQL without connecting. A `DATABASE_URL` set in the shell takes precedence over `.env`. The migrations only add columns or tables, so the code already deployed keeps working while a database is migrated ahead of a deploy.

Run the API:

```bash
uvicorn api:app --reload
```

Interactive docs at `http://127.0.0.1:8000/docs`.

Run the tests. The database tests need an empty database named `compliance_test` (create it once, add `TEST_DATABASE_URL=postgresql://user:password@localhost:5432/compliance_test` to `.env`, then build its schema); without it they are skipped, and the rest still run:

```bash
pip install -r requirements-dev.txt
alembic -x db=test upgrade head
pytest
```

Each database test runs inside a transaction that is rolled back afterwards, so the database stays empty. GitHub Actions runs the same steps on every push, against a fresh Postgres, and lints and builds the dashboard.

The frontend is a separate repository: [compliance-dashboard](https://github.com/rajkhatri08/compliance-dashboard).

---

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Always HTTP 200: `status` (`ok` or `degraded`), whether the database answers, documents and chunks indexed, and the documents that failed to index at startup, each with a reason |
| POST | `/query` | Ask a question; returns answer, tier, reason, `refused` (true when there is no answer), warnings, sources, distances and `timings_ms` (retrieval, generation, audit write, total) |
| GET | `/documents` | Document inventory with computed expiry status |
| POST | `/upload` | Upload a `.txt` or `.pdf` (at most 10 MB, 300 pages and 300 chunks, or 100 chunks on the live server); stored in Postgres with its vectors, indexed immediately, unapproved by default |
| POST | `/documents/{filename}/approve` | Approve a document |
| POST | `/documents/{filename}/unapprove` | Revoke approval |
| POST | `/documents/{filename}/retire` | Retire a document: removed from the index, kept in the audit trail |
| GET | `/audit` | Full audit log, newest first: each row's answer and the exact chunks it used |

Errors, each with a plain message in `detail`:
- `400` on an empty question, or an upload's name, owner, version or review date
- `404` on an unknown document
- `409` on a duplicate filename
- `413` on an upload over 10 MB, a PDF over 300 pages, or a text over the chunk limit
- `422` on an upload with no extractable text, a password-protected or unreadable PDF, or a .txt that isn't UTF-8
- `500` if indexing an upload fails (nothing is saved), and `{"detail": "internal error"}` for any other unexpected failure on any route; the details go to the server log, never to the client
- `503` when the model is unavailable (Gemini answers 408, 429, 500, 502, 503 or 504, times out after a minute, or can't be reached), or while another upload is being indexed

Every error response, the 500s included, carries the CORS headers, so the dashboard can show it.

---

## Known limitations

These are real and worth stating plainly.

**Governance changes require a restart to affect retrieval.** Uploaded documents are indexed immediately and searchable straight away. But approving, revoking or retiring a document writes to Postgres without updating the chunk metadata already held in the index, so the change does not affect answers until the service restarts. Making it immediate would need chunk ids tracked per document so the affected entries could be updated or deleted in place.

**Retirement is one-way.** There is no reinstate endpoint. A retired document can only be brought back by editing the database directly.

**Only the extracted text is kept, not the uploaded file.** The original PDF is never stored. An improvement to PDF cleaning can't be applied to documents uploaded before it, because their raw text is gone. The seed documents can be re-extracted from `documents/`.

**The index is rebuilt in memory on every start, and nothing limits its total size.** Each start reads every chunk's stored vector from Postgres and adds it to an in-memory ChromaDB collection: 0.6 seconds for 736 chunks, where embedding them again took 53 seconds. But the collection lives in the server's 512 MB, and each upload adds to it. A persistent vector store (for example pgvector) is the scale fix.

**Uploads are slow on the free server.** Embedding takes about 0.2 CPU-seconds per chunk on a laptop. On Render's free instance, a 16-page PDF of 71 chunks took 3 minutes 34 seconds from upload to searchable, about 3 seconds per chunk (timed 22 Nov). The live server therefore limits uploads to 100 chunks, about 5 minutes; a longer document is refused within seconds and must be split into smaller files.

**Tables come through as lines.** pypdf reads a table row by row, and rows are packed into chunks with their neighbours, so a question answered by one row competes with prose on the same topic.

**The 10 MB limit is checked after the upload has arrived.** The web framework receives the whole request, buffering anything over 1 MB in a temporary file, before the endpoint can measure it. An oversized upload is refused with a clear message, but only once it has been sent in full.

**PDF cleaning is heuristic.** The line-joining regex uses punctuation and capitalisation to guess where a sentence continues across a line or page break. The furniture rule recognises a header or footer by its repetition at the page edges. The hyphen rule rejoins a word split at a line end, so a compound broken at its own hyphen ("pre-train") loses the hyphen. They work on the documents tested, but they are guesses, not rules, and they will misjudge some layouts. A scanned PDF is refused, not read: there is no OCR.

**The 1.2 relevance cutoff was chosen by inspection**, not calibration. Unlike the tier thresholds, it has not been measured, and it is a known weak point. Short, casually phrased questions produce weaker embedding matches than formally worded ones and can fall outside it even when the corpus does answer them.

**The evaluation set is 48 questions**, all written and labelled by one person. An answer's facts are checked by substring, which can pass a wrong answer that contains the phrase ("not 72 hours") and miss a correct one worded differently. Large enough to compare configurations against each other on the same questions; too small to claim an absolute accuracy figure with confidence.

**The model can ignore the refusal instruction.** A refusal is recognised by the token the prompt asks for (`NO_ANSWER_IN_CONTEXT`). A model that answers a question its sources don't cover, or refuses in its own words without the token, isn't caught: its reply is shown as an answer and tiered by its sources. In the P0.4 evaluation, each of the model's 14 refusals was the bare token and no answer refused in words ([docs/eval/p0.4.md](docs/eval/p0.4.md)), but that is 41 replies from one model version. A reply that mixes a partial answer with the token is refused, by design.

**Tests check logic, not answers.** The unit tests cover the tier rules and their order, the refusal check, the chunker, PDF cleaning, every upload check, chunk ids, the audit row, the mapping of model failures to 503 (through the real Gemini SDK on a simulated network) and the CORS headers on error responses. The database tests run against a real Postgres built by the migrations: storing an upload with its vectors, a duplicate refused by the database's own unique constraint, the clean-up after a failed index, the approve, revoke and retire endpoints, and a question's audit row. CI runs them all on every push. Whether an answer is right is the evaluation's job, not the tests'. Still untested: two uploads of one name racing each other (the test reproduces the second insert, not the race), the startup handler as a whole (its parts are tested separately), and the dashboard's behaviour (CI only lints and builds it).

**Heading detection is pattern-based.** The chunker recognises two kinds of heading: a line that is exactly `Art. N GDPR` (a long article is then split at its numbered paragraphs, `1.`, `2.` …) and a line starting `Section N.`. A document structured any other way — "Article 5", "§ 3", "1.1" — gets the fallback: lines packed into chunks of up to 1,000 characters. It splits only between lines, so in a hard-wrapped text file a chunk can end mid-paragraph, and a single line over 1,500 characters is cut into 1,000-character pieces overlapping by 50, wherever the cuts fall.

**Audit rows written before the migration are incomplete.** They have no answer and no chunk hashes, and their chunk ids are the old positional ones (`chunk_5` meant whatever was sixth when the index was built), which no longer resolve. Their times are in the local time of whichever machine wrote them (IST or UTC) with no zone recorded, and a question with no relevant source shows a distance of `0.0` instead of none. They are left as they were written: the audit log is append-only.

**An audit row identifies the text it used but doesn't keep it.** The content hash proves which text an answer was drawn from. If a document's text later changes, the hash shows that it changed, not what it said before. Keeping that would need a history of document versions.

**Cold starts.** Render's free tier sleeps after 15 idle minutes, and the next request waits for a full start. Measured in Render's logs on 22 Nov, the app's own start took about 49 seconds: 29 s importing its libraries on a tenth of a CPU, then 20 s of startup, which included downloading the embedding model (79 MB) from S3 every time. A restart or deploy took about 3 minutes in all; the rest is Render's. From P0.4, Render's build downloads the model once, and a start loads it from disk. The 29 s of imports remain the largest part.

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
