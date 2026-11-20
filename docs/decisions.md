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

---

## 19 Nov 2026 · P0.1: the evaluation runs the production code path

Commit: the one that adds this entry.

**Context.** The v1 evaluation (`evaluate.py`) measured a different pipeline from `/query`. It sent the top 3 chunks to the model with no relevance filter, indexed from `metadata.json` instead of Postgres, never scored tiers, and its accuracy was counted by hand. The README's chunking figures (78%, 90%, 95%) describe that harness, not what production does.

**Decision.**
- One code path. `pipeline.answer_question` holds what `/query` did, and `/query` is a thin wrapper around it; startup indexing moved to `pipeline.index_documents`. The API and the evaluation build and query the index with the same code.
- A dedicated eval database, `compliance_eval`, set to a fixed governance state by `evals/seed_eval_db.py`, and a fixed date (2026-11-18), so results don't change by themselves when a review date passes. The scripts refuse any other database name and any Neon host.
- 48 pre-registered questions in `evals/questions_v2.py`: v1's 23 verbatim plus 25 new ones. Each carries expected sources, facts and tier; the conflict questions also carry the floor and each policy's verdict, for P2.4. The labels follow one rule: a document counts for a question when its text makes a claim about that exact question. The author reviewed every label against the document text, made five fixes, and froze the set before any real answer was seen.
- Automatic scoring (`evals/run_eval.py`, `evals/scoring.py`): refusal, expected facts as case-insensitive phrases, and tier, each over a denominator fixed by the labels. A model call that still fails after retrying 429s and 503s is counted as failed, never as wrong. Answers are cached on the question, the exact chunk ids and hashes, the prompt template's hash and the model name.

**Alternatives considered.**
- Fixing `evaluate.py` in place: it would still have been a second copy of the pipeline, drifting whenever `/query` changed. It was deleted.
- Grading answers with a second model: harder to see when it is wrong, and it makes the evaluation depend on another model. Phrase matching is crude but transparent.
- Running against the development database: its governance state changes as documents are approved and retired, so results would drift.

**Consequences and limitations.**
- One person wrote every question and label, and 48 questions is small. Phrase matching can pass a wrong answer that contains the phrase and fail a correct one worded differently.
- The model is not deterministic. The committed results file is the record; the cache is local and gitignored.
- The cache invalidates by design when chunk ids, the prompt or the chunking change (→ P0.2, P0.4, P0.5).
- `answer_question` still reports any generation failure as "model unavailable", as before (→ P0.4).
- The fake-model smoke run, done before the labels were frozen, showed which questions retrieve nothing under the cutoff. No question was reworded because of it.

**How it was verified.**
- Before and after the refactor, with a fake model and the audit writes captured, all 48 questions gave identical answers, tiers, sources, distances, warnings and audit writes, and the 400 and 503 paths matched.
- 12 unit tests of the scoring. Seeding the eval database twice gave the same state. A fake-model smoke run checked that a 429 is counted as failed, that a bug stops the run, that the cache hits, and that retries back off and then give up.
- The baseline, 19 Nov: refusal 36/48, facts 17/34, tier 28/48, no failed calls. Every miss traces to retrieval. 17 questions list Art. 12 among their expected sources and it passed the filter for none of them; half the tier misses (10 of 20) come from a whole-document policy chunk passing the filter, or failing to. Details in `docs/eval/baseline.md`.

---

## 20 Nov 2026 · P0.5: chunks follow each document's structure

Commit: the one that adds this entry.

**Context.** The chunker understood only `Art. N GDPR` headings. That is why, on 30 Aug, two test policies were given invented GDPR headings, "Art. 99 GDPR" and "Art. 100 GDPR", so that they would be chunked (`89010a1`). Both are false: GDPR has 99 articles, Art. 100 doesn't exist, and Art. 99 is "Entry into force and application". The files were public on GitHub. Every other document without those headings became a single chunk. The P0.1 baseline traced every miss to retrieval. Whole, Art. 12 (3,868 characters) passed the filter for none of the 17 questions that need it. Half the tier misses came from a whole-policy chunk passing the filter or failing to.

**Decision.**
- **Split where the document's own structure splits**, the idea behind article chunking applied one level down and to policies. `ingest.chunk_document` tries three rules in order:
  1. **Articles.** A line that is exactly `Art. N GDPR` starts an article. An article over 1,500 characters is split before each line starting `N. ` (a numbered paragraph). A lettered list, a sentence number such as "3The" and the "Suitable Recitals" lines stay with their paragraph, because they don't start that way.
  2. **Sections.** A line starting `Section N.` starts a section.
  3. **Neither.** Lines are packed into chunks of up to 1,000 characters. A single line over 1,500 is cut by the old `chunk_text` (1,000 characters, 50 overlap).
- **Every piece carries its context.** The lines before the first heading become a prefix on every piece: the article's heading and title (`Art. 12 GDPR | Transparent information, … | 3. …`), or the policy's title and version (`Data Retention Policy | Version 1.0 | Section 2. …`). A paragraph then says which article and document it comes from, both to the embedding and to the model. The eval's sources metric also relies on each piece containing its heading.
- **The 1,500-character rule was fixed before measuring**, and all four GDPR articles are over it (12: 3,868; 17: 2,686; 15: 2,197; 33: 1,986), so all four were split: 4 chunks became 20. The baseline had expected only Art. 12 to split.
- **The two policies were rewritten with honest headings:** a title ("Information Security Policy", "Vendor Management Policy"), "Version 1.0" and `Section 1.` with their original topic line. Every obligation sentence is byte-identical.
- `chunk_by_article` stays as the first step of `chunk_document`, now anchored to whole heading lines. `calibrate.py` still uses it alone, so its historical result reproduces. `/upload` and startup indexing both call `chunk_document`.
- **The five regressions stay as measured.** Changing the chunker again after seeing these results would tune it to them. v1-09, 0.003 over the cutoff, belongs to P3.2's cutoff sweep.
- **The README's opening example now quotes the new distances** (0.673 against 0.837, from 0.743 against 0.894) and says they come from the structure-aware chunker, so it describes the code that is deployed. The expired policy still ranks first.

**Alternatives considered.**
- **Raising the threshold so that only Art. 12 splits.** It is the smallest change aimed at the measured misses. Rejected: the threshold would have been chosen after looking at the documents, the tuning that pre-registration exists to prevent.
- **Packing blank-line paragraphs**, as first planned. `clean_text` removes blank lines before chunking, so the fallback can only see lines. In the seed files each paragraph is one line.
- **An em dash or a hyphen as the prefix separator.** `json.dumps` writes `—` as `—` in the results files read by hand, and a hyphen is ambiguous next to the draft's "0.3 - DRAFT". ` | ` has neither problem.
- **One section per obligation in the two rewritten policies**, matching the retention policy's layout. Rejected: it would have meant inventing section titles. Only the false heading was replaced.
- A library text splitter: rejected for the reason in the 22 Aug entry; the boundaries are the point.

**Consequences and limitations.**
- **Five questions got worse**, each explained in `docs/eval/p0.5.md`. The clearest weakness of paragraph chunks is Art. 17(3), the exceptions to erasure. It opens "Paragraphs 1 and 2 shall not apply …" and depends on paragraph 1, so on its own it no longer reaches two erasure questions (1.203 and 1.471).
- **The draft AI policy's breach section is further from breach questions than the whole draft was.** On conflict-01 it moved from 1.170 to 1.256, and it now passes for none of the five breach questions (one before). Those tiers come back SILVER, not the labelled BRONZE, and P2.3's compliance card would not see the draft's 24-hour rule there.
- **Retention §3 (access requests) passes for five GDPR-only request questions** and turns GOLD into SILVER. The whole policy did so for six.
- **No crowding.** With 26 chunks instead of 6, the top 5 could have filled with one article's paragraphs and pushed out a needed chunk. A retrieval-only check found none (below), so there is no reason yet for a per-document cap.
- **The sources metric became more lenient for articles and stricter for policies.** Any paragraph of an article contains its heading, so any paragraph counts; only the right policy section counts.
- **More questions reach the model**: 41 calls against 31, and 10 of the 14 out-of-corpus refusals now come from the model rather than the filter. No out-of-corpus question was answered.
- **Heading detection is pattern-based.** "Article 5", "§ 3" or "1.1" fall back to line packing.
- `clean_pdf_text` replaces the `ﬁ` and `ﬂ` ligatures but not `ﬀ` (`gdpr1.pdf` reads "aﬀect"). Noticed here, not fixed (→ P0.6).

**How it was verified.**
- 14 new unit tests (`tests/test_ingest.py`) cover each rule and its edge cases: lettered lists, sentence numbers, recitals, an article citation that isn't a heading, packing order and overlap, empty text. They also check the seed files: 20 GDPR pieces, each naming its article, and each policy's sections under its title with no "GDPR" in any of them. The security and vendor cases failed on the invented headings until the files were rewritten. All 26 tests pass.
- The chunks of all six seed files were printed before and after: 4 → 20 (`gdpr_ch3.txt`, and `gdpr1.pdf` alike), 1 → 3 (retention, draft), 1 → 1 (security, vendor). The longest piece is 1,391 characters. Stripping the prefixes and joining the pieces gives back each old chunk's text, in order.
- `calibrate.py` printed the same result as before (1.02, 17 of 23, 73.9%).
- **Evaluation v2, 20 Nov** (`evals/results/2026-11-20-p0.5.json`). The questions, prompt, model, cutoff and top-5 are the same as in the baseline; only the corpus changed. Refusal 36 → 41 of 48, facts 17 → 26 of 34, tier 28 → 31 of 48, sources 12 → 22 of 34, 0 failed calls. Group by group in `docs/eval/p0.5.md`. The paraphrase group's tier fell from 4/8 to 2/8.
- **The crowding check** ranked all 26 chunks for every question, with no model calls. No expected source and no answer-holding chunk was under the cutoff but below 5th, before or after. Its per-question results match both eval runs row by row on the sources metric.

---

## 20 Nov 2026 · P0.2: migrations and a traceable audit trail

Commit: the one that adds this entry.

**Context.** Three gaps made the audit trail hard to rely on. The schema was changed by hand: `retired` was added with an `ALTER TABLE` typed into each database, and nothing recorded which schema a database was on. Chunk ids were positions (`chunk_0`, `chunk_1` … in load order, then an in-memory counter for uploads), so after a restart or a document change an id in an old audit row could point at different text or at nothing, and two concurrent uploads could take the same number. And the audit row recorded neither the answer nor the text it was drawn from. When no chunk passed the filter it stored a top distance of `0.0`, which means a perfect match, the opposite of what happened.

**Decision.**
- **Alembic for every schema change.** `migrations/env.py` builds its engine from `os.getenv("DATABASE_URL")`, so a value set in the shell wins over `.env` (python-dotenv never overrides a variable that is already set). It never passes the URL through Alembic's config, where ConfigParser would read the `%` of a percent-encoded password as interpolation. It stops if the variable is unset and logs `host/database` before connecting. `alembic -x db=eval …` targets `compliance_eval` through the existing `use_eval_database()` checks. `alembic.ini` deliberately has no `sqlalchemy.url`.
- **Revision 0001 is the schema as it was**: `audit_log` and `documents`, including `retired`. Existing databases join with `alembic stamp 0001`, after their schema has been compared with what 0001 creates. New databases run `alembic upgrade head`. `init_db()` (SQLAlchemy's `create_all`) is deleted.
- **Revision 0002 is additive.** It adds three nullable columns with no default and leaves existing rows untouched:
  - `audit_log.answer`
  - `audit_log.chunks`: JSON, one `{chunk_id, source, content_hash, distance}` per chunk that passed the filter, best first
  - `audit_log.created_at`: `timestamptz`, written in UTC
- **Downgrades refuse.** 0001's would drop every table; 0002's would delete recorded answers from an append-only log. Because the migrations only add, a rollback means redeploying the previous code, which ignores the new columns. It never needs a schema downgrade.
- **Chunk ids come from content**: `{filename}::{index}::{first 12 hex of sha256(text)}`. They are built in one place, `pipeline.chunk_ids_and_metadata` (using `retrieve.make_chunk_id`), for startup indexing and `/upload` alike. Chroma metadata gains `chunk_index` and the full 64-character `content_hash`. The counter is gone.
- **The audit row.**
  - `top_distance` is `None` when nothing passed, and the early-return row records its real answer text.
  - `sources` and the old `timestamp` are still written, so the code from before P0.2 keeps working on new rows if it is ever redeployed.
  - `/audit` reports `created_at` in UTC; Postgres hands it back in the session's time zone, IST locally.
  - `/audit` sorts by id instead of by the mixed-zone timestamps, and returns `null` for the fields old rows don't have.
  - `log_query_db` takes its fields by keyword only (`*` in the signature), so `answer` and `reason`, both strings, can't be swapped by position.
- **`print_schema.py`**: a read-only schema print. Its first statement is `SET TRANSACTION READ ONLY`, the host goes to stderr, and types are spelled the way PostgreSQL spells them. Its output for revision 0001 is committed as `migrations/baseline_schema.txt`, so Neon can be compared with it before Neon is stamped at Ship 1.
- **Frontend.** The Audit page shows "—" for a missing distance and a collapsed "Answer and sources": the answer, and each chunk id with its distance. It also works against the old backend.

**Alternatives considered.**
- **Converting `audit_log.timestamp` to `timestamptz`.** One column instead of two, but not additive, and it would rewrite every past audit row with a guessed zone: rows written from the Mac in IST would move 5.5 hours. A new column leaves history as it was recorded.
- **Writing UTC into the old column.** No schema change, but the value would still carry no zone, and new UTC rows would look the same as old IST rows.
- **Random ids (UUIDs)**, as the README once suggested. They can't collide, but they change on every restart, so an audit row still couldn't be matched to the index, and an id would say nothing about the text. A content hash does both.
- **Storing each chunk's text in the audit row.** A complete record, but about 7 KB per question, and the text already lives in the documents. The hash proves which text was used.
- **Reporting old rows' `0.0` as `null`.** The rule would be exact: only no-source rows have empty `sources`. But the log would then report something other than what was recorded. Old rows are left as written, and the README says so.
- **A full eval run.** Ids are never sent to the model, so about 41 model calls could only have measured the model's own randomness. A retrieval-only comparison is exact and free. P0.4 changes the prompt, so its run misses the cache anyway.

**Consequences and limitations.**
- **Old audit rows stay as recorded.** They keep:
  - positional ids that no longer resolve
  - no answer and no chunk hashes
  - timestamps in the local time of whichever machine wrote them: IST from the Mac, UTC from Render
  - `0.0` on "no source" rows
- **A hash identifies the text but doesn't keep it.** If a document's text changes, an old row shows that its text differed, not what it said. P0.3 stores the current text in Postgres; there is no text history.
- **The eval's cache keys include chunk ids**, so the next full run makes fresh model calls. `corpus_sha256` changes too, although no chunk text did.
- **`audit_log` now has two time columns.** `/audit` uses `created_at` when a row has one.
- **Ship order at Ship 1:** the frontend must deploy before the backend. The old Audit page calls `toFixed` on the distance and fails on `null`.

**How it was verified.**
- **The baseline matches what exists.** A fresh database built by revision 0001 printed a schema byte-for-byte identical to the local and eval databases. Alembic's comparison found exactly three additions between 0001 and the models, and after 0002 `alembic check` found no differences.
- **The real databases migrated cleanly.** The local and eval databases were stamped at 0001 and upgraded. `alembic check` is clean on both, and their 15 and 96 old rows are untouched.
- **Downgrade refused.** On the scratch database it was refused and left the database at 0002.
- **`--sql`** showed 0002's SQL: three `ADD COLUMN`s and the version bump, in one transaction.
- **Shell wins.** A `DATABASE_URL` set in the shell overrode `.env`: Alembic logged the shell's database name.
- **`print_schema.py` is read-only.** A write after its `SET TRANSACTION READ ONLY` was refused.
- **No retrieval change.**
  - For all 48 eval questions, the top 5 chunks matched the P0.5 results file exactly: source, distance, first line and order.
  - Every id matched its chunk's text, and two separate processes produced the same 26 ids.
  - `calibrate.py` printed the same result (1.02, 17 of 23, 73.9%).
- **Smoke test against the eval database**, with a stub model:
  - A breach question's row recorded the answer, five chunks with their hashes, best distance 0.700 and a UTC time.
  - An off-topic question's row recorded `top_distance: null` and `chunks: []`.
- **Tests.** 13 new unit tests, 39 in all: ids, the audit row and how it is read, and the pipeline's audit write against a fake index. Frontend lint and build pass.

---

## 20 Nov 2026 · P0.3: durable, safe uploads; a startup that reads Postgres

Commit: the one that adds this entry.

**Context.**
- **Uploads didn't survive a restart.** They were written to `documents/` on Render's ephemeral disk and lost on the next restart, while their row stayed; H0 only made startup skip them.
- **`/upload` did things in the wrong order.** It inserted the row first (check-then-insert, so two simultaneous uploads could race), then wrote the file, then extracted the text. A broken PDF left a row behind with a 500.
- **No limits.** Nothing limited size or type, and `file.filename` was used as a path.
- **Startup ran on import.** It ran when `api.py` was imported, and read files from disk.
- **A dead-connection failure.** SQLAlchemy's pool kept connections that Neon closes when its free compute sleeps, so the first query after a pause could fail.

**Decision.**
- **Migration 0003, additive**, adds three columns to `documents`:
  - `content`: the extracted, cleaned text
  - `content_sha256`
  - `created_at`: UTC; null for older rows, because when they were created isn't known
- **An upload is prepare → insert → index.**
  - **Prepare.** `upload.prepare_upload` runs every check before anything is stored, cheapest first. Each failure is an `UploadRejected(status, message)`.
    - **The name** is NFC-normalised. Allowed: letters, digits and combining marks in any script, plus space, `. _ - ( ) & , ' +`, and the typographic `’ ‘ – —` that Word and macOS insert. Not allowed: `/ \ : # ? %` and control characters. A `:` would make chunk ids ambiguous; `#`, `?` and `%` break the document URLs. No leading dot, at most 200 characters, `.pdf` or `.txt` in any case.
    - **Then** the owner, version and review date; the size (10 MB, 413); the text, extracted in memory (422 for no text, an unreadable PDF or a .txt that isn't UTF-8); and the chunks.
  - **Insert.** `db.insert_document` stores the row with its text in one transaction. A duplicate is detected from Postgres's unique-violation code (SQLSTATE 23505) on `documents` → 409. It isn't checked before the insert, and it isn't identified by the constraint's name, which hasn't been verified on Neon.
  - **Index.** If indexing fails, `db.delete_document` removes the row, and the upload reports 500 "nothing was saved".
  - **The route is a plain `def`.** It runs in a worker thread, so parsing and embedding a large file don't block other requests. Nothing is written to disk.
- **Startup is a lifespan handler.**
  - It reads the non-retired rows with their text, retrying an `OperationalError` after 1, 2, 4 and 8 s, and fails fast if the database is still unreachable.
  - It indexes each document separately and keeps the results on `app.state`.
  - Importing `api.py` touches neither the database nor Gemini, so the tests can import it.
- **`/health` always returns 200**: `ok` or `degraded`, a live `SELECT 1`, the counts, and each document that failed with a fixed reason.
- **The engine** gets `pool_pre_ping=True` and `connect_timeout=10`.
- **`backfill_content.py`** fills `content` from `documents/` with the same `extract_text` as uploads.
  - It is idempotent, runs in one transaction, and has `--dry-run` and `--retire-unfillable`.
  - It reads only files that resolve inside `documents/`. Rows from before H0's name check could have a name like `../.env`, and reading that would store a secret as a document.
- **The eval database's fixed state includes the text.** `seed_eval_db.py` sets it the same way.

**Alternatives considered.**
- **`os.path.basename` on the name**, as first planned. It would silently turn `../x.txt` into `x.txt`. Rejecting `/` and `\` says what's wrong, and the allowlist makes basename redundant.
- **The plan's ASCII allowlist `[A-Za-z0-9._ -]`.** Safe, but it refuses names real people use: accents, "report (1).pdf", "Q&A". It was widened by decision, to the characters that are safe in a URL path, a chunk id and the database.
- **Detecting a duplicate by the constraint name `documents_filename_key`.** Exact, but it assumes Neon names the constraint the same way. The error code and the table don't depend on a name.
- **Starting with an empty index if the database is down.** It would look healthy and answer "no relevant source" to everything. Failing fast is fail-closed, and Render restarts the service.
- **Keeping the row when indexing fails**, to be indexed at the next restart. The document would become searchable later without the user knowing. An upload either fully happens or leaves nothing, and the user can retry.
- **Storing the original file (bytea).** It would allow re-extraction with better cleaning, but at up to 10 MB per upload on a 0.5 GB free database. The text is what the system needs.
- **Running the backfill on the eval database.** The seed script owns the eval database's fixed state, so a fresh eval database takes two commands.

**Consequences and limitations.**
- **Only the extracted text is kept.** Later improvements to PDF cleaning, such as P0.3b's page-furniture removal or P0.6's ligature fix, can't be applied to earlier uploads.
- **Every cold start re-embeds every stored chunk**, so large uploads make startup slower. P0.3b measures it.
- **The 10 MB limit is checked after the request body has arrived.** Starlette buffers the body (to a temporary file past 1 MB), so the limit bounds memory, not transfer.
- **A crash during indexing leaves a committed row**, which the next start retries. A document that exhausts memory would do so again; P0.3b's limits are the guard.
- **A failed cleanup leaves a row behind.** If indexing fails and the compensating delete fails too, the row stays and is indexed at the next start, although the user was told nothing was saved. This is logged.
- **The name rule still refuses some characters people use,** such as `;`, `!`, `@`, `[`, `]` and emoji. The message lists the offending characters and what is allowed.
- **Ship 1 order.** Neon must be migrated and backfilled before the new backend starts. A backend that starts against a database without content indexes nothing.

**How it was verified.**
- **64 new unit tests, 103 in all**, none touching a database or the model:
  - every name, field, size and text rule
  - the API returning each rejection's status without calling the database or the index
  - a duplicate giving 409
  - an indexing failure removing the row and returning 500
  - `/health` when ok and when degraded
  - startup with no documents, with a document without text, and with one that fails to index
  - duplicate detection by code and table
- **End to end against a scratch database**, upgraded to 0003 and dropped afterwards, through the real startup:
  - two uploads stored and indexed: a name with parentheses, and a PDF with an umlaut in its name
  - the same name again: 409, from the real unique violation
  - `../x.txt` 400, an 11 MB file 413, an image-only PDF 422
  - a forced indexing failure: 500, with no row left
  - after a second startup built from Postgres alone, the uploaded text was still the best match for a question about it
  - `/documents` sends no content
- **Local databases.** The dev and eval databases are at 0003, with `alembic check` clean. The backfill filled 3 rows on the dev database and reported `h0_missing_file_test.txt` (retired, no file); a second run changed nothing.
- **Retrieval unchanged.** With the text read from Postgres, all 48 eval questions' top 5 match the P0.5 results, and the 26 chunk ids are byte-identical to P0.2's. `calibrate.py` printed the same result. No model calls.
