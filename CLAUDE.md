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

`POST /query` calls `pipeline.answer_question`, the same function the eval calls: retrieve top 5 chunks → keep those with distance < 1.2 (the single relevance filter) → warnings from their metadata → Gemini answers from those chunks only → `classify()` → audit row in Postgres → response `{question, answer, tier, reason, warnings, sources, distances}`.

Tier rules (`govern.py`), in order: model refused → BRONZE · any relevant source unapproved → BRONZE · any relevant source past review date → SILVER · else GOLD.

Other endpoints: `GET /health`, `GET /documents`, `POST /upload` (multipart; new documents unapproved), `POST /documents/{filename}/approve|unapprove|retire`, `GET /audit`.

Startup (when `api.py` is imported) calls `pipeline.index_documents`: each active document's file in `documents/` is loaded, cleaned and chunked into the index. A document whose file is missing or unreadable (`OSError`, `UnicodeDecodeError`, pypdf's `PyPdfError`) is skipped with a logged warning; `GET /health` returns `{"status": "ok", "skipped_documents": [...]}`. `POST /upload` rejects a filename that is empty, starts with `.`, or contains `/` or `\` with 400, before any write.

## Files (backend)

| File | Role |
|---|---|
| `api.py` | FastAPI routes. `/query` delegates to `pipeline.answer_question`; the index is built at import time by `pipeline.index_documents` (moves to a lifespan handler in P0.3) |
| `pipeline.py` | `index_documents` (startup indexing) and `answer_question` (the body of `/query`: retrieve, filter, warnings, generate, classify, audit row); `ModelUnavailable` → 503. Shared by the API and the eval |
| `ingest.py` | Load .txt/.pdf, `clean_text`, `clean_pdf_text`, `chunk_by_article`, legacy `chunk_text`, `load_metadata` (used by `migrate.py`, `calibrate.py`) |
| `retrieve.py` | Chroma `build_index`, `search`, `add_to_index` |
| `generate.py` | `PROMPT_TEMPLATE`, `MODEL` and the Gemini call |
| `govern.py` | `RELEVANCE_CUTOFF`, `is_expired(review_date, today=None)`, `is_refusal`, `classify` — the tier rules |
| `db.py` | SQLAlchemy models `AuditEntry` (`audit_log`), `Document` (`documents`) and helpers |
| `eval_questions.py` | v1 question set (23), untouched; still used by `calibrate.py`. Copied verbatim into `evals/questions_v2.py` |
| `evals/` | Evaluation v2: `questions_v2.py` (48 frozen questions and labels), `run_eval.py` (runner, cache, retries), `scoring.py` (pure scoring), `seed_eval_db.py` and `eval_db.py` (the `compliance_eval` database and its safety checks), `results/` (committed runs), `cache/` (gitignored) |
| `tests/`, `pytest.ini` | Unit tests: the eval's scoring (from P0.1); P0.6 adds the rest |
| `docs/eval/baseline.md` | The P0.1 baseline: numbers per group, every miss with its cause |
| `calibrate.py` | Historical tier-threshold calibration (the thresholds were removed) |
| `migrate.py` | One-off seed of `documents` from `documents/metadata.json` |
| `documents/` | Seed documents. `metadata.json` covers only the first three |
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
pytest                                         # scoring tests now; the full suite from P0.6
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

- Schema changes go through **Alembic** (from P0.2). Never use `create_all` to change an existing table.
- Migrations are **additive** (new nullable columns or new tables, with defaults), so the code already deployed keeps working between "migrate Neon" and "deploy".
- Raj runs migrations and backfills against Neon himself, by hand, during a ship checkpoint. Never do it for him.
- Tests use a disposable local database; the test setup refuses any URL containing `neon.tech`.

## Evaluation

- From P0.1 the eval calls the same function as `/query`, against a dedicated local eval database seeded to a fixed governance state, with a fixed "today".
- The eval database is `compliance_eval` (`EVAL_DATABASE_URL` in `.env`); the scripts refuse any other database name and any `neon.tech` host. The eval date is fixed at 2026-11-18. The labels in `evals/questions_v2.py` are frozen since the P0.1 baseline.
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
