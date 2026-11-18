# Design decisions

Why this system is built the way it is: one entry per decision, oldest first. New entries go at the bottom.

Each entry has five parts: **Context** (the problem), **Decision**, **Alternatives considered**, **Consequences and limitations**, and **How it was verified**. The commits listed under each heading contain the change.

The entries up to and including H0 were written on 18 November 2026 from `readme.md`, the git log and the author's recollection. Where the record doesn't say why something was done or how it was checked, the entry says "not recorded" instead of guessing. A sentence marked *(inference)* is a likely explanation, not a recorded fact.

An arrow such as (→ P0.3) points to the upgrade task that addresses the limitation; that task adds its own entry below.

---

## 19 Aug 2026 · Semantic retrieval instead of keyword scoring

Commits: `0a3aaf1`, `cf49d5f`, `9779867`, `668d4db`, `4b10300`, `addf791`

**Context.** The first retriever ranked chunks by counting keyword matches. On the first sample text (a general passage about the universe, before the switch to GDPR), 35 of 54 chunks tied at score 4, so they could not be ranked (`0a3aaf1`). On the GDPR text, a breach-notification question returned Art. 17 (the right to erasure) instead of Art. 33, which sets the 72-hour rule (`cf49d5f`).

**Decision.** Retrieve by meaning. The `all-MiniLM-L6-v2` model turns every chunk into an embedding: a vector of numbers that places texts with similar meaning close together. ChromaDB stores the vectors and returns the chunks closest to the question's embedding, with their distances (`9779867`, `addf791`).

**Alternatives considered.** Keyword scoring, tried first and dropped for the results above. Any others: not recorded.

**Consequences and limitations.**
- Distance measures how related a chunk's topic is to the question, not whether the chunk contains the answer. "Distance thresholds removed from the tier" below shows the cost.
- The model was first loaded through sentence-transformers and PyTorch, which later broke the deploy (see "Chroma's ONNX embedder instead of sentence-transformers").

**How it was verified.** On individual questions, before the evaluation set existed (it arrived on 20 Aug, `fe4f517`): semantic retrieval returned Art. 33 where keyword search had returned Art. 17 (`668d4db`); a question sharing no vocabulary with Art. 33 still retrieved it (`4b10300`); Chroma's distances confirmed Art. 33 (`addf791`).

---

## 22 Aug 2026 · Chunk on article headings

Commits: `063306a`, `1bf69cd`, `c9d474a`, `8a03040`

**Context.** With 300-character chunks the model refused questions the corpus does answer. Art. 15's enumerated list of data-subject rights was cut mid-clause, so the chunk holding the heading stopped before point (c), the answer the question needed (`063306a`). At 1000 characters the same failure came back one level deeper, in Art. 17(3) (README).

**Decision.** Split each document at its `Art. N GDPR` headings:

```python
re.split(r"(?=Art\. \d+ GDPR)", document)
```

The lookahead `(?=...)` matches the position just before each heading without consuming it, so every chunk keeps its own title (`c9d474a`).

**Alternatives considered.** Fixed-size character chunks with 50 characters of overlap, measured with the v1 harness (`evaluate.py`) on the 20-question set of the time:

| Strategy | Accuracy | False refusals | False answers |
|---|---|---|---|
| 300 characters, 50 overlap | 78% (14/18) | 3 | 1 |
| 1000 characters, 50 overlap | 90% (18/20) | 2 | 0 |
| Article headings | 95% (19/20) | 1 | 0 |

A LangChain text splitter was not used: it hides chunk boundaries and overlap behaviour, which are exactly what decided these results (README).

**Consequences and limitations.**
- The 300-character run is scored out of 18 questions, the other two out of 20. Why: not recorded; most likely two questions hit API errors, which `evaluate.py` prints as `FAILED` and leaves unscored *(inference)*. (→ P3.2, which re-runs all three strategies on one denominator.)
- The v1 harness is not the `/query` pipeline. It retrieves the top 3 chunks with no relevance filter and indexes from `documents/metadata.json`; `/query` retrieves the top 5, keeps those under distance 1.2 and reads Postgres. It also computes no score: it prints one line per question, and those rows were counted by hand (→ P0.1).
- The chunker only recognises GDPR article headings. A document without them becomes a single chunk (→ P0.5).

**How it was verified.** The three runs in the table, recorded in `1bf69cd` and `c9d474a`. Accuracy stayed at 95% once the index held three documents (`8a03040`). No later run is recorded.

---

## 23 Aug 2026 · Governance as an ordered veto

Commits: `226cdab`, `511103b`, `b69beee`, `594607a`, `dc0e955`, `ff5fa3b`, `ce65874`, `212c3cc`, `7ca5efc`

**Context.** Similarity alone picked the wrong source. Asked how long an organisation has to report a data breach, vector search ranked an internal retention policy, past its review date since January 2023, above the current GDPR article: distance 0.743 against 0.894. The policy says 30 days; GDPR says 72 hours (`226cdab`, README). An unapproved draft AI policy was the closest match in the whole evaluation set, at 0.720, and it was answered from like any other source (`dc0e955`).

The rules took their current shape on 23 Aug. Reading a 15-row audit log showed questions that GDPR clearly answers coming back BRONZE. Fixing that exposed the next flaw, and so on: six in one afternoon.

1. Irrelevant chunks downgraded tiers: an unapproved draft retrieved as the 5th-nearest chunk forced BRONZE. Fix: only chunks under distance 1.2 count for governance (`ce65874`).
2. SILVER meant two different things, an expired source or a mediocre match. Fix: every tier carries a reason (`212c3cc`).
3. Refusals were tiered as moderate confidence. Fix: the refusal check comes first and always gives BRONZE (`212c3cc`).
4. The distance thresholds were unreliable. Fix: a calibration script found a 73.9% ceiling, so the thresholds were removed (`7ca5efc`; see "Distance thresholds removed from the tier").
5. Governance and generation saw different chunk sets, so an answer using an expired source could come back GOLD. Fix: one filter, applied before both (`7ca5efc`).
6. Unanswerable queries were missing from the audit trail. Fix: the early-return path, taken when no chunk passes the filter, logs them too (`7ca5efc`).

**Decision.** The tier is decided by deterministic checks in `govern.py`, in order; the first that matches decides (`ff5fa3b`, `212c3cc`):

1. The model refused → BRONZE, "no answer found in the available sources".
2. Any relevant source is unapproved → BRONZE, naming the source.
3. Any relevant source is past its review date → SILVER, naming the source.
4. Otherwise → GOLD, "answered from an approved, current source".

Expired drops an answer to SILVER and unapproved to BRONZE on purpose: an expired document was approved once and may still be broadly right, so it needs review, not rejection; a draft never carried authority (README). Every tier carries a reason (`212c3cc`).

One relevance filter, distance under 1.2, is applied once, before generation. The chunks that pass it are the only ones used for the answer, the warnings, the tier and the audit row (`7ca5efc`).

**Alternatives considered.** A weighted score combining similarity and governance. Rejected: a very close match could outweigh the fact that nobody approved the document. Checking approval first and returning at once makes that impossible (README).

**Consequences and limitations.**
- Not fail-closed yet. The approval check is `approved == False`, so a value of `None` would pass as approved, and a malformed review date raises an error instead of counting as past review (→ P0.4).
- A refusal is detected by the substring "don't know" in the answer.
- Governance is copied into each chunk's metadata when the index is built, so approving, revoking or retiring a document changes no answer until the service restarts (→ P1.1).

**How it was verified.** The draft's 0.720 match is classified BRONZE (`ff5fa3b`), and the conflicting breach deadlines are surfaced with warnings that name each source (`b69beee`, `594607a`). The v1 harness never scores tiers (→ P0.1).

---

## 23 Aug 2026 · Distance thresholds removed from the tier

Commits: `a4e2e7f`, `ff5fa3b`, `60f7bce`, `7ca5efc`

**Context.** The first tiering also used the top chunk's distance: above 1.1 → BRONZE ("no strong match in the corpus"), above 0.95 → SILVER ("moderate match only") (`ff5fa3b`). Reviewing the audit log (added in `60f7bce`) showed misclassifications in both directions, so the thresholds were calibrated against the labelled questions rather than defended by intuition (README). It was the fourth of the six flaws found on 23 Aug (see "Governance as an ordered veto"). The evaluation set had already shown, on 20 Aug, that distance alone cannot separate answerable from unanswerable questions (`a4e2e7f`).

**Decision.** `calibrate.py` tries every threshold from 0.80 to 1.50 in steps of 0.01 against the 23 labelled questions. The best, 1.02, classifies 17 of 23 correctly (73.9%), and no threshold can do better, because the two groups overlap: the top distances of the 10 answerable questions run from 0.868 to 1.368, those of the 10 out-of-corpus questions from 1.057 to 1.644. Questions about appointing a data protection officer and about children's consent, neither answerable from this corpus, score closer than several answerable ones, because Art. 12 mentions both topics in passing (README). So the thresholds were removed (`7ca5efc`): the tier rests only on the model's refusal and on document governance. The 1.2 cutoff remains, but it only decides which chunks are considered, not what the answer is worth.

**Alternatives considered.** Keep the thresholds, re-tuned to 1.02: still wrong on 6 of the 23 questions.

**Consequences and limitations.**
- The 1.2 cutoff was chosen by inspection and never calibrated (README). Measured on 18 Nov 2026, while writing this entry, not at the time of the decision (retrieval only, same three documents): 2 of the 10 answerable questions (both about Art. 12, at 1.314 and 1.368) and 1 of the 3 conflict questions (1.214) have no chunk under 1.2, so `/query` would answer "No sufficiently relevant source found"; 4 of the 10 out-of-corpus questions have a chunk under 1.2 and reach the model (→ P3.2, which sweeps the cutoff).
- `calibrate.py` indexes from `documents/metadata.json` rather than Postgres, and looks only at the top chunk's distance.

**How it was verified.** Re-run on 18 Nov 2026: `calibrate.py` printed the same result (best threshold 1.02, 17 of 23, 73.9%), and the distance ranges above were recomputed from the same index.

---

## 23 Aug 2026 · Chroma's ONNX embedder instead of sentence-transformers

Commits: `9779867`, `d49f4b5`

**Context.** The embedding model was first loaded through sentence-transformers, which runs on PyTorch (`9779867`). `pip freeze` wrote the CUDA (GPU) build of PyTorch into `requirements.txt`; that pulled roughly 2 GB of GPU libraries onto Render's 512 MB instance, and the deploy was killed with SIGKILL (README).

**Decision.** Use ChromaDB's built-in embedding function. It runs the same `all-MiniLM-L6-v2` model through ONNX Runtime, at about 79 MB (`d49f4b5`, README). ONNX is a portable format for trained models, and ONNX Runtime runs them without PyTorch.

**Alternatives considered.** Not considered at the time; in hindsight, a CPU-only PyTorch build is the obvious comparison.

**Consequences and limitations.**
- `CLAUDE.md` forbids `pip freeze`, torch and sentence-transformers because of this.
- The README says "same embeddings, same results", but no commit records an evaluation run after the switch (→ P0.1, whose baseline measures the embedder that runs now).

**How it was verified.** The H0 deploy on 17 Nov built and served normally on the 512 MB instance. Search quality after the switch: not recorded.

---

## 24 Aug 2026 · Document metadata in Postgres

Commits: `9b3fd3a`, `6b65e73`, `c8fa49f`, `9f8a43b`

**Context.** Each document's governance metadata (owner, version, approved, review date) lived in `documents/metadata.json`, a file in the repo. On Render that file lived on an ephemeral disk, so an approval written there would vanish on the next redeploy, which is the same reason the audit log had moved from a JSON file to Postgres the day before (`9b3fd3a`, `6b65e73`). And the approve and unapprove endpoints, added in the next commit (`9f8a43b`), needed state they could change.

**Decision.** A `documents` table in Postgres holds each document's filename (unique), owner, version, approval and review date; the `retired` flag came later. Startup builds the index from these rows (`9f8a43b`), and `migrate.py` seeded the table once from `metadata.json` (`c8fa49f`). Whether a document is past review is worked out on each request from its review date, not stored, because a stored flag goes stale the day it should flip (README).

**Alternatives considered.** Not recorded.

**Consequences and limitations.**
- Governance is read into the index at startup, so a change in Postgres reaches answers only after a restart (→ P1.1).
- Schema changes are made by hand: the `retired` column was added with a manual `ALTER TABLE` on each database, as the README records (→ P0.2).
- `evaluate.py` and `calibrate.py` still read `metadata.json`, which lists only three of the six seed documents (→ P0.1).

**How it was verified.** Not recorded.

---

## 30 Aug 2026 · Uploads arrive unapproved

Commits: `89010a1`, `dca4dfd`

**Context.** A senior data engineer pointed out that reviewers expect to add their own documents, so real file upload was built instead of a paste-text box. An upload (`.txt` or `.pdf`) is chunked and added to the live index, searchable at once (`89010a1`).

**Decision.** Every uploaded document starts unapproved, in both its database row and its chunk metadata. It can be retrieved, but it cannot produce a GOLD answer until someone approves it; answers drawn from it are BRONZE, with a warning naming it (`89010a1`, README). The review date and a duplicate filename are checked before anything is written (`dca4dfd`).

**Alternatives considered.** A paste-text box instead of real file upload, rejected for the reason above.

**Consequences and limitations.**
- Anyone can upload, and anyone can approve: no write needs authentication (→ P1.2).
- On Render an uploaded document cannot reach GOLD today. An approval reaches the index only after a restart, and the restart deletes the uploaded file, because the disk is ephemeral (→ P0.3, P1.1).
- The database row is created before the file is written, so a failed write would leave a row with no file (→ P0.3).

**How it was verified.** Not recorded.

---

## 30 Aug 2026 · Retire, never hard-delete

Commits: `3cf972c`, `6465fdc`; frontend `bc72374`

**Context.** A document needs a way to stop answering questions without destroying the record of what it said.

**Decision.** Retiring sets `retired` on the document's row. Startup leaves retired documents out of the index, and the row and the file stay (`3cf972c`). The dashboard gained a Retire button (frontend `bc72374`).

**Alternatives considered.** Hard deletion. Rejected because audit entries name their sources: deleting a document would leave past entries pointing at something that no longer exists, with no way to know what it said (README).

**Consequences and limitations.**
- Retirement takes effect only after a restart, and it is one-way: there is no reinstate endpoint (→ P1.1).
- Anyone can retire a document: no authentication (→ P1.2). There was no confirmation step either until H0.
- The reason above holds only in part today. An audit row stores chunk ids such as `chunk_3`, numbered by a counter that starts again at every startup, so an old row cannot be traced reliably to its document; only its warnings name files (→ P0.2).

**How it was verified.** Not recorded. The confirmation added in H0 was seen on the live site on 17 Nov.

---

## 17 Nov 2026 · H0: skip unreadable documents at startup

Commits: `8d9e3e8`; frontend `349754a`

**Context.** Render's free instance has an ephemeral disk: an uploaded file is lost on restart while its row stays in Postgres. Startup crashed with `FileNotFoundError` opening it, so the service could never come back up (`8d9e3e8`).

**Decision.** Startup skips a document whose file is missing or unreadable (`OSError`, `UnicodeDecodeError`, or pypdf's `PyPdfError`), logs a warning naming it, and lists it under `skipped_documents` in `GET /health`. Any other exception still crashes startup: it means a bug, not a bad file. The same hotfix also:
- makes `/upload` reject a filename that is empty, starts with `.`, or contains `/` or `\` (400), before any write. It had allowed path traversal: a name such as `../api.py` would have been written outside `documents/`.
- pins `requirements.txt` to the tested versions, so a rebuild cannot pull untested major releases (google-genai 3.x, pypdf 7.x).
- makes Retire in the dashboard ask for confirmation, naming the document, and removes a stray test file from the frontend repo (`349754a`).

**Alternatives considered.**
- Catching every `Exception`: rejected, because a bug would then look like a skipped file.
- Storing each document's text in Postgres: the real fix, planned separately; this is a stopgap until then (→ P0.3).

**Consequences and limitations.**
- A skipped document is no longer searchable, and an uploaded document still disappears after a restart (→ P0.3).
- `build_index` raises `ValueError` on an empty list of chunks, so startup still crashes if no document can be indexed (→ P0.3).
- The Retire confirmation says the document stops answering "after the next backend restart", which stops being true once governance changes apply immediately (→ P1.1).

**How it was verified.** Locally, before the fix, a row with no file crashed startup; after it, startup succeeds with the same 6 chunks and `/health` lists the row; path-like filenames return 400 with nothing written (`8d9e3e8`). On the live site on 17 Nov: the Render deploy went live, `/health` returned `{"status": "ok", "skipped_documents": []}`, a live question returned SILVER, and Retire asks for confirmation. Render's build log installed the pinned versions as `cp314` wheels (packages pre-built for Python 3.14), so Render runs Python 3.14.

---

## 18 Nov 2026 · P0.0: work on a branch; keep test tools out of production

Commit: the one that adds this file.

**Context.** Every push to `main` deploys production in both repos (Render for the backend, Vercel for the frontend), and the upgrade is two weeks of changes. Tests arrive in P0.6: they need pytest, and FastAPI's `TestClient` needs httpx. The running app doesn't need pytest, and it gets httpx only indirectly, through google-genai and chromadb.

**Decision.**
- All upgrade work happens on a branch called `upgrade` in both repos. `main` moves only at a ship checkpoint, fast-forwarded to `upgrade` once the tests pass.
- Test tools live in `requirements-dev.txt`, which includes `requirements.txt` and adds `pytest==9.2.0` and `httpx==0.28.1`. Render installs only `requirements.txt`, so the test tools never reach the 512 MB server.
- httpx is pinned directly, although google-genai and chromadb already install it, because the tests will use it directly. The pin is the version those packages already resolve to, so the two files cannot disagree today.

**Alternatives considered.**
- Committing straight to `main`: every mistake would reach production at once.
- Adding pytest and httpx to `requirements.txt`: the production server would install a test tool it never runs.

**Consequences and limitations.**
- Pushing the frontend's `upgrade` branch, which still pointed at the same commit as `main`, triggered no Vercel deployment: GitHub shows no new status or deployment after the push. Vercel builds a preview of a pushed branch by default, so one is likely with the first new commit on `upgrade` *(inference)*. A preview cannot call the backend, because CORS allows only `localhost:3000` and the production site; production is unaffected either way.
- When google-genai or chromadb needs a different httpx, the pin must move; pip reports the conflict.

**How it was verified.** `git branch --show-current` printed `upgrade` in both repos before any file was written. Both branches were pushed, and `git branch -vv` shows each tracking `origin/upgrade`. `pip install -r requirements-dev.txt` found every package in `requirements.txt` and httpx 0.28.1 already installed at their pinned versions, and added only pytest 9.2.0 and the two packages it needs, pluggy 1.7.0 and iniconfig 2.3.1. `pip check` found no broken requirements, `pytest --version` printed `pytest 9.2.0`, and `from fastapi.testclient import TestClient` imports. The Render dashboard shows no deploy after 17 Nov, so pushing `upgrade` deployed nothing.
