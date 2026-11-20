# CLAUDE.md — Source-Governed RAG

RAG over regulatory and internal compliance documents in which **document governance overrides similarity ranking**. Every answer is tiered GOLD / SILVER / BRONZE by deterministic rules about its sources (approved or not, past its review date or not, active or retired), and every tier carries a reason.

The developer is Raj, an early-career engineer who must be able to explain every line of this code. Optimise for code he can defend, not for cleverness. The task list is `UPGRADE_PLAN.md`.

## Repositories

| Repo | Local path | GitHub | Deploys to |
|---|---|---|---|
| Backend (this repo) | `~/PycharmProjects/Compliance_App` | `rajkhatri08/source-governed-rag` | Render free instance — **512 MB RAM, ephemeral disk** — https://source-governed-rag.onrender.com |
| Frontend | `../compliance-dashboard` (added with `--add-dir`) | `rajkhatri08/compliance-dashboard` | Vercel — https://compliance-dashboard-omega-amber.vercel.app |

Database: PostgreSQL. Local Postgres 18 for development; **Neon (Singapore) is production**. **Both repos auto-deploy from `main`.**

- Backend: Python 3.14 (`.venv`), FastAPI, SQLAlchemy + psycopg2, ChromaDB in memory with its built-in ONNX `all-MiniLM-L6-v2` embedder, Google Gemini via `google-genai` (model `gemini-3.6-flash`), pypdf.
- Frontend: Next.js 16 App Router, React 19, **JavaScript, not TypeScript**, Tailwind 4, Recharts. Client components with `useState`/`useEffect`. API base URL from `process.env.NEXT_PUBLIC_API_URL`.

## How it works today

`POST /query` calls `pipeline.answer_question`, the same function the eval calls: retrieve top 5 chunks → keep those with distance < 1.2 (the single relevance filter) → warnings from their metadata → Gemini answers from those chunks only → `classify()` → audit row in Postgres → response `{question, answer, tier, reason, warnings, sources, distances}`. The audit row (`db.new_audit_entry`) records the answer and each passing chunk as `{chunk_id, source, content_hash, distance}` in `chunks`. `top_distance` is `None` when nothing passed, never `0.0`, and `created_at` is UTC (`timestamptz`). `sources` and the old zone-less `timestamp` are still written for code from before P0.2. `GET /audit` sorts by id and returns `null` for fields that rows from before P0.2 lack.

Tier rules (`govern.py`), in order: model refused → BRONZE · any relevant source unapproved → BRONZE · any relevant source past review date → SILVER · else GOLD.

Other endpoints: `GET /health`, `GET /documents`, `POST /upload` (multipart; new documents unapproved), `POST /documents/{filename}/approve|unapprove|retire`, `GET /audit`.

`POST /upload` (a plain `def`, so it runs in a worker thread) reads at most 10 MB + 1 byte, then `upload.prepare_upload` runs every check before anything is stored, cheapest first:
- **Name:** NFC-normalised. Letters, digits and combining marks in any script, plus space and `. _ - ( ) & , ' + ’ ‘ – —`. No `/ \ : # ? %` or control characters; no leading dot; ≤200 characters; `.pdf`/`.txt` in any case.
- **Fields:** owner ≤100, version ≤50, a real `YYYY-MM-DD`.
- **Size:** ≤10 MB, else 413.
- **Text** (`extract_text`): PDFs in memory via pypdf; .txt as UTF-8, with the BOM dropped, line endings normalised and NULs removed; then `clean_text`. No text, an unreadable PDF or a non-UTF-8 .txt → 422.
- **Chunks:** `chunk_document`.

Each failure is an `UploadRejected(status, message)`. Then `db.insert_document` stores the row and its `content` in one transaction; a duplicate is Postgres's unique violation (SQLSTATE 23505 on `documents`), giving `DuplicateDocument` → 409. Then `pipeline.index_document`; if that fails, `db.delete_document` removes the row → 500 "nothing was saved". Nothing is written to disk.

Startup is a FastAPI lifespan handler (importing `api.py` touches neither the database nor Gemini):
- It creates the Gemini client and an empty index (`retrieve.new_index`).
- It reads `db.read_documents_to_index()` (non-retired rows with their stored `content`), retrying `OperationalError` after 1, 2, 4 and 8 s, then failing fast: startup stops and Render restarts it.
- `pipeline.index_documents(collection, docs)` indexes each document separately and returns the filenames indexed and the failures (`{filename, reason}`: "no stored content" or "indexing failed").
- The results go on `app.state`: `client`, `collection`, `indexed_documents`, `failed_documents`.

`GET /health` always returns 200: `{"status": "ok"|"degraded", "database": <SELECT 1 succeeds>, "documents_indexed", "chunks_indexed", "failed_documents"}`. The engine uses `pool_pre_ping` (Neon closes connections when it sleeps) and `connect_timeout=10`. `documents/` is seed data only.

Indexing chunks each document with `ingest.chunk_document`, which follows the document's structure: a GDPR article over 1,500 characters becomes one chunk per numbered paragraph, a policy one chunk per `Section N.`, each piece prefixed with its heading and title (`Art. 12 GDPR | <title> | 3. …`); a document with neither is packed line by line into chunks of up to 1,000 characters. Each chunk's id is `{filename}::{index}::{sha256(text)[:12]}` (`retrieve.make_chunk_id`, built for startup and `/upload` alike by `pipeline.chunk_ids_and_metadata`), so ids are the same after every restart; its Chroma metadata carries `chunk_index` and the full `content_hash`.

## Files (backend)

| File | Role |
|---|---|
| `api.py` | FastAPI routes and the lifespan handler (startup: read the stored text with retries, index it, keep the results on `app.state`). `/query` delegates to `pipeline.answer_question`; `/upload` is prepare → insert → index |
| `upload.py` | `prepare_upload` and its checks (`check_filename`, `check_fields`, `check_size`, `extract_text`, `pdf_pages`, `decode_text`), the limits, `UploadRejected(status, message)`, `PreparedUpload`. Writes nothing. `extract_text` is also used by `backfill_content.py` and `seed_eval_db.py`, so stored text is extracted one way |
| `backfill_content.py` | Fills `documents.content` for rows without it, from `documents/<filename>` (only files really inside `documents/`: an old row named `../.env` can't pull in a secret). One transaction, idempotent; `--dry-run`, `--retire-unfillable` |
| `pipeline.py` | `chunk_ids_and_metadata` (ids and Chroma metadata for one document's chunks), `index_document` (one document; startup and `/upload`), `index_documents` (startup: each document separately, returns indexed and failed) and `answer_question` (the body of `/query`: retrieve, filter, warnings, generate, classify, audit row); `ModelUnavailable` → 503. Shared by the API and the eval |
| `ingest.py` | `load_document` (a seed .txt by path, for `calibrate.py` and the tests), `clean_text`, `clean_pdf_text`, `chunk_document` (structure-aware chunking) and its helpers, `chunk_by_article` (its first step; `calibrate.py` still uses it alone, so the historical calibration reproduces), `chunk_text` (cuts a line over 1,500 characters), `load_metadata` (used by `migrate.py`, `calibrate.py`) |
| `retrieve.py` | `content_hash`, `make_chunk_id`; Chroma `new_index` (an empty collection), `add_to_index` (ids from the caller), `search` |
| `generate.py` | `PROMPT_TEMPLATE`, `MODEL` and the Gemini call |
| `govern.py` | `RELEVANCE_CUTOFF`, `is_expired(review_date, today=None)`, `is_refusal`, `classify` — the tier rules |
| `db.py` | SQLAlchemy models `AuditEntry` (`audit_log`), `Document` (`documents`) and helpers; `new_audit_entry` and `audit_entry_to_dict` build and read an audit row without a database; `insert_document` (duplicate → `DuplicateDocument`, from `is_unique_violation`), `delete_document`, `read_documents_to_index`, `database_reachable`. No `init_db`: schema comes from the migrations |
| `alembic.ini`, `migrations/` | Alembic. `env.py` (engine from `os.getenv("DATABASE_URL")`; `-x db=eval` targets the eval database), `versions/` (`0001` baseline, `0002` audit `answer`, `chunks`, `created_at`, `0003` `documents.content`, `content_sha256`, `created_at`), `baseline_schema.txt` (what 0001 creates, as `print_schema.py` prints it). Downgrades refuse by design |
| `print_schema.py` | Read-only schema print (`SET TRANSACTION READ ONLY`; host and revision to stderr), to compare a database with `migrations/baseline_schema.txt` before `alembic stamp 0001` |
| `eval_questions.py` | v1 question set (23), untouched; still used by `calibrate.py`. Copied verbatim into `evals/questions_v2.py` |
| `evals/` | Evaluation v2: `questions_v2.py` (48 frozen questions and labels), `run_eval.py` (runner, cache, retries), `scoring.py` (pure scoring), `seed_eval_db.py` and `eval_db.py` (the `compliance_eval` database and its safety checks), `results/` (committed runs), `cache/` (gitignored) |
| `tests/`, `pytest.ini` | Unit tests, none touching a database or the model: the eval's scoring (P0.1), the chunker (`test_ingest.py`, P0.5), chunk ids (`test_ids.py`), the audit row (`test_audit.py`), `answer_question`'s audit write and startup indexing against fakes (`test_pipeline.py`), every upload check (`test_upload.py`), `/upload` and `/health` through `TestClient` without the lifespan (`test_api.py`), duplicate detection (`test_db.py`). `pdf_samples.py` builds test PDFs in memory. P0.6 adds the rest |
| `docs/eval/` | `baseline.md`: the P0.1 baseline, numbers per group, every miss with its cause. `p0.5.md`: structure-aware chunking against the baseline, with a retrieval-only crowding check |
| `calibrate.py` | Historical tier-threshold calibration (the thresholds were removed) |
| `migrate.py` | One-off seed of `documents` from `documents/metadata.json` |
| `documents/` | Seed documents only: the app never reads or writes it at runtime (`backfill_content.py`, `seed_eval_db.py`, `calibrate.py` and the tests read it). `metadata.json` covers only the first three |
| `readme.md` | Public README (lowercase filename) |
| `requirements-dev.txt` | Test tools on top of `requirements.txt`: pinned `pytest` and `httpx` (FastAPI's `TestClient` needs httpx). Render installs only `requirements.txt` |
| `docs/decisions.md` | Decision log, oldest first. Every task appends an entry (see "Working on a task") |
| `audit_log.json`, `cache.json` | Local leftovers, gitignored. Don't use |
| `UPGRADE_PLAN.md` | Task list. Local only, gitignored, never commit |

## Run locally

```bash
source .venv/bin/activate
uvicorn api:app --reload                       # http://127.0.0.1:8000 (docs at /docs)
cd ../compliance-dashboard && npm run dev      # http://localhost:3000
pip install -r requirements-dev.txt            # app + test tools (pytest, httpx)
pytest                                         # unit tests; the full suite from P0.6
alembic upgrade head                           # dev database to the latest migration (logs host/database first)
alembic -x db=eval upgrade head                # the same for compliance_eval
alembic upgrade 0001:head --sql                # print the migrations' SQL; connects to nothing
alembic check                                  # do the models and the database match?
python print_schema.py                         # read-only schema print; diff it with migrations/baseline_schema.txt
python backfill_content.py --dry-run           # what would be filled from documents/; then run it without --dry-run
python -m evals.seed_eval_db                   # eval database to its fixed state (EVAL_DATABASE_URL in .env)
python -m evals.run_eval --label <name>        # eval v2; writes evals/results/<date>-<name>.json
```

- Frontend `.env.local` holds `NEXT_PUBLIC_API_URL`. For local work Raj sets it to `http://127.0.0.1:8000` and restarts `npm run dev` (Next.js reads env files only at start), and sets it back to the Render URL afterwards. Vercel uses its own setting, not this file.
- If Next.js starts on port 3001, an old dev server still holds 3000, and CORS only allows `http://localhost:3000`. `lsof -i :3000` finds it.
- Raj runs uvicorn and `npm run dev` in his own terminals so he can watch the logs.

## Safety rules — never break these

1. **Which database?** Before running anything that touches a database (the API, a script, a migration, the eval), print only the host:
   `python -c "import os; from dotenv import load_dotenv; from urllib.parse import urlparse; load_dotenv(); print(urlparse(os.getenv('DATABASE_URL')).hostname)"`
   If it contains `neon.tech`, **stop and ask Raj**. Neon is production. Development, tests and the eval never touch it.
2. **Secrets.** Never open, print, `cat`, `grep` or echo `.env`, `.env.local`, or anything holding an API key, a database URL with a password, or a reviewer token. Read configuration in code with `os.getenv`. Never put a secret in a commit, a log line, a test fixture or the chat.
3. **Git.** Work on branch `upgrade` in both repos. Never push to `main` unless Raj explicitly allows it for that step — a push to `main` deploys production. No `push --force`, `reset --hard`, `git clean`, rebase or branch deletion without asking. Stage files by name, never `git add .` or `git add -A`. Never commit `.env*`, `cache.json`, `audit_log.json`, `.DS_Store`, `.idea/`, `UPGRADE_PLAN.md` or `evals/cache/`.
4. **Destructive SQL** (`DROP`, `TRUNCATE`, `DELETE` without a narrow `WHERE`, dropping columns): ask first, every time, on every database.
5. **Dependencies.** Never `pip freeze > requirements.txt` — it once captured GPU PyTorch (about 2 GB of CUDA libraries) and the 512 MB deploy was SIGKILLed. Direct dependencies only, pinned with `==`. No torch, sentence-transformers, LangChain, LlamaIndex or RAGAS. Ask before adding any dependency. A dependency ships in the same commit as the code that imports it (two past deploys broke on this).
6. **Render's disk is ephemeral.** Anything the app writes to disk at runtime is gone after the next restart or spin-down. Durable state lives in Postgres.

## Design principles

1. **Governance is a veto, not a weight.** The tier is decided by deterministic code in `govern.py`, never by the model.
2. **The LLM extracts; code decides.** The model may draft answers and extract claims. Code verifies every extracted claim against the source text and makes every decision.
3. **Fail closed.** Approved means `approved is True`; `False`, `None`, a missing row or unreadable state all mean unapproved. An unparseable review date counts as past review.
4. **One relevance filter, applied once.** The chunks that pass it are the only chunks used for generation, warnings, tier, conflicts and the audit row.
5. **Postgres is the source of truth** for document text, governance state and history; the vector index is a rebuildable cache. From P1.1, governance is read from Postgres at query time, so changes take effect immediately.
6. **Uploads default to unapproved.** A reinstated document comes back unapproved and needs a fresh approval.
7. **Retire, never hard-delete.** `audit_log` and `governance_events` are append-only.
8. **Validate before side effects.** Reject bad input before any database write or index change.
9. **Never return exception details to clients.** Catch specific exceptions; log with `logging` (`logger.exception` for tracebacks); return a short message. A bug must never be reported as "model unavailable".
10. **Every tier and verdict carries a human-readable reason.**
11. **Measure, don't assume.** Behaviour changes get before/after numbers on the same question set. Report regressions honestly.
12. **Pre-registered evaluation.** Once a question set is committed with its baseline, never edit its questions or labels to improve a metric. New questions go in a new, separately reported group.
13. **Simple beats clever.** No speculative abstractions, frameworks or layers the task doesn't need.

## Code style

- Plain, explicit Python. Small functions with type hints; a one-line docstring when the name isn't enough. f-strings; `zip`/`enumerate` over index loops; two blank lines between top-level definitions; match the surrounding style.
- When replacing code, delete the old version: no duplicate routes, functions or imports, no commented-out code.
- Frontend: keep the existing look (Tailwind utilities, the tier badge colours). Every `fetch` checks `res.ok` and has an error state. Filenames in URLs go through `encodeURIComponent`.

## Database

- Schema changes go through **Alembic** (from P0.2). Never use `create_all` to change an existing table. New revision: `alembic revision -m "<what>" --rev-id 000N`, written by hand, then `alembic check` against a database at head.
- Migrations are **additive** (new nullable columns or new tables, with defaults), so the code already deployed keeps working between "migrate Neon" and "deploy". `downgrade()` raises: a rollback is a redeploy of the old code, never a schema downgrade.
- State on 20 Nov: local `compliance` and `compliance_eval` are at `0003` with their content filled. Neon is not yet stamped. At Ship 1, before any deploy, Raj does the following, each with the `DATABASE_URL='<Neon URL>'` prefix:
  1. Compares `python print_schema.py` with `migrations/baseline_schema.txt`. Any difference other than a server default on `retired` → stop.
  2. Runs `alembic stamp 0001`, `alembic upgrade head` and `alembic check`.
  3. Runs `python backfill_content.py --dry-run`, then `python backfill_content.py --retire-unfillable`.

  Only then does the frontend deploy, then the backend. A backend that starts against a database without content indexes nothing.
- `audit_log` has two time columns: `timestamp` (old, no zone, the writing machine's clock: IST from the Mac, UTC on Render) and `created_at` (UTC, from P0.2).
- Raj runs migrations and backfills against Neon himself, by hand, during a ship checkpoint. Never do it for him.
- Tests use a disposable local database; the test setup refuses any URL containing `neon.tech`.

## Evaluation

- From P0.1 the eval calls the same function as `/query`, against a dedicated local eval database seeded to a fixed governance state, with a fixed "today".
- The eval database is `compliance_eval` (`EVAL_DATABASE_URL` in `.env`); the scripts refuse any other database name and any `neon.tech` host. Its schema comes from `alembic -x db=eval upgrade head`; `seed_eval_db.py` sets the rows and their content (from `documents/`, through `upload.extract_text`). The eval date is fixed at 2026-11-18. The labels in `evals/questions_v2.py` are frozen since the P0.1 baseline.
- The cache key covers everything that can change a result: question, the exact chunk ids and hashes sent, prompt-template hash, model name.
- Gemini 429/503: retry with exponential backoff. If it still fails, record the question as **failed** — reported separately, never scored as a wrong answer, never dropped. Every configuration is reported over the same denominator.

## Working on a task

1. Read the task in `UPGRADE_PLAN.md`. Inspect the code it touches before proposing anything.
2. Propose a plan — files to change, tests to add, risks — and **wait for Raj to say go**.
3. Implement in small steps. Run `pytest` (from P0.6). Run the eval when the task says so and show the numbers beside the previous run.
4. Append an entry to `docs/decisions.md`: **Context · Decision · Alternatives considered · Consequences and limitations · How it was verified**.
5. Update "Known limitations" in `readme.md` when the task fixes or creates one, and keep "How it works today" and the Files table in this file current.
6. Finish with: what changed, in plain language; how Raj can verify it by hand; what could break.
7. Commit only when Raj says so: one task, one commit per repo, message like `P1.2: require a reviewer token for governance changes`. Then tick the task's box in `UPGRADE_PLAN.md` and add one line under it: the commit hash and anything the next task must know.

Explain in plain language, and define any term an early-career engineer might not know the first time it appears (for example: migration, idempotent, lifespan handler, timing attack).
