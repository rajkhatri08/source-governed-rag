# Evaluation baseline (P0.1)

The first run of evaluation v2: the 48 pre-registered questions in `evals/questions_v2.py`, run through the code `/query` runs, against a fixed governance state. Every later change is measured against these numbers, on the same questions, group by group.

## The run

| | |
|---|---|
| Date | 19 Nov 2026, label `baseline` |
| Results | `evals/results/2026-11-19-baseline.json`, one row per question |
| Code | the P0.1 commit (the run recorded `19514c5` plus the then-uncommitted P0.1 changes) |
| Model | `gemini-3.6-flash`: 31 calls, 0 failed, 0 answers from the cache |
| Pipeline | `pipeline.answer_question`: top 5 chunks, relevance cutoff 1.2 |
| Corpus | 6 chunks: Arts 12, 15, 17 and 33 of `gdpr_ch3.txt`, and each policy file as a single chunk |
| Governance | GDPR approved and current; retention policy approved but past review (2023-01-15); draft AI policy unapproved. Eval date fixed at 2026-11-18 |
| Questions | 48, frozen before this run; `questions_v2.py` sha256 `839e2d037b5c21c8…` |

## What the metrics mean

- **Refusal:** the system refused (no chunk passed the filter, or the model said it didn't know) exactly when the question is out of corpus. Scored on all 48.
- **Facts:** the answer contains every expected fact, as a case-insensitive phrase, any listed alternative counting. Scored on the 34 answerable questions.
- **Tier:** the tier equals the label, which is the tier rules applied to the documents whose text addresses the question. Scored on all 48.
- **Sources** (diagnostic): every expected source passed the relevance filter. Scored on the 34 answerable questions.
- **Failed:** a model call that still failed after its retries. Counted apart, never as wrong, never dropped. There were none.

## Numbers

| Group | n | Refusal | Facts | Tier | Sources |
|---|---|---|---|---|---|
| v1-answerable | 10 | 7/10 | 7/10 | 4/10 | 5/10 |
| v1-out_of_corpus | 10 | 10/10 | — | 10/10 | — |
| v1-conflicted | 3 | 2/3 | 1/3 | 1/3 | 0/3 |
| paraphrase | 8 | 5/8 | 3/8 | 4/8 | 2/8 |
| governance | 3 | 2/3 | 2/3 | 2/3 | 2/3 |
| conflict | 6 | 4/6 | 2/6 | 3/6 | 1/6 |
| no_conflict_expected | 4 | 2/4 | 2/4 | 0/4 | 2/4 |
| out_of_corpus_hard | 4 | 4/4 | — | 4/4 | — |
| **Overall** | **48** | **36/48 (75%)** | **17/34 (50%)** | **28/48 (58%)** | **12/34 (35%)** |

Conflict detection is scored from P2.4. The system has none yet, so its baseline is zero conflicts found on the 6 conflict questions (and, trivially, zero false alarms on the 4 others).

## What the misses have in common

26 of the 48 questions miss on refusal, facts or tier. Every one of those misses traces to retrieval, not to the model inventing an answer:

- **The chunk with the answer never reached the model.** In all 17 fact misses, the chunk holding the answer in force did not pass the 1.2 filter. 17 questions list Art. 12, the longest article, among their expected sources, and it passed the filter for none of them; the closest it came was 1.273.
- **Ten answerable questions never reach the model at all:** no chunk under 1.2, so `/query` returns "No sufficiently relevant source found". Short, casual phrasings are among them (paraphrase-01 at 1.204, paraphrase-02 at 1.242).
- **Whole-document policy chunks move tiers.** Each policy is one chunk. The retention policy passed the filter for six GDPR-only questions and turned GOLD into SILVER. The draft AI policy, which holds a 24-hour rule for AI-system breaches, stayed above 1.2 on three breach questions, so they came back SILVER rather than BRONZE.
- **No out-of-corpus question was answered.** All 14 were refused: 7 by the filter, 7 by the model.
- **Two correct tiers are coincidences:** paraphrase-02 and conflict-06 never reached the model, and the no-source BRONZE happens to equal their label.

## Every miss and its cause

A distance measures how far a chunk is from the question; smaller is closer, and the cutoff is 1.2.

**No chunk under 1.2, so the question never reached the model.** Refusal, facts and tier all miss, except where noted.

| Question | Closest chunk | Chunk with the answer |
|---|---|---|
| v1-04 · free of charge | Art. 15, 1.314 | Art. 12, 1.387 |
| v1-07 · access-request deadline | Art. 33, 1.368 | Art. 12, 1.511; retention, 1.407 |
| v1-22 · access-request deadline | draft, 1.214 | Art. 12, 1.342; retention, 1.227 |
| paraphrase-01 · will my data be shared? | Art. 15, 1.204 | the same, 0.004 over the cutoff |
| paraphrase-02 · report a leak (tier coincidentally right) | Art. 33, 1.242 | the same |
| paraphrase-08 · ignore my request | Art. 15, 1.462 | Art. 12, 1.510 |
| governance-03 · plain language | Art. 12, 1.391 | the same |
| conflict-06 · challenging an algorithm's decision (tier coincidentally right) | draft, 1.273 | Art. 12, 1.697 |
| no_conflict_expected-01 · manifestly unfounded request | Art. 15, 1.455 | Art. 12, 1.578 |
| no_conflict_expected-02 · ID before a request | Art. 15, 1.381 | Art. 12, 1.429 |

**Chunks passed, but not the one with the answer.**

- **v1-05** (refusal, facts, tier): Arts 17, 15 and 33 passed; Art. 12(4) at 1.273 did not. The model said "I don't know", a correct refusal of what it was given, scored as a false refusal.
- **conflict-03** (refusal, facts, tier): only the draft passed (1.182); Art. 12 was at 1.313 and the retention policy outside the top 5. The model refused, so BRONZE instead of SILVER.
- **v1-23 and conflict-05** (facts): only the draft passed (0.720 and 1.005), so the answer was the draft's 14 days; Art. 12(3)'s one month was at 1.306 and 1.554. The tier, BRONZE, is right.
- **paraphrase-04** (facts): only Art. 15 passed (1.125), so the answer explained the fee for further copies; "free of charge" is in Art. 12 (1.343).
- **paraphrase-06** (facts): the retention policy passed (0.989) and Art. 12 was outside the top 5, so the answer was the expired policy's 60 days. The tier, SILVER, is right.
- **conflict-04** (facts, tier): as paraphrase-06 (60 days), and the draft passed too (1.144), so BRONZE instead of SILVER.

**The right answer, but a policy chunk changed the tier.**

- **v1-02, v1-09, paraphrase-03, paraphrase-07, no_conflict_expected-03, no_conflict_expected-04** (tier): GOLD expected, SILVER returned. The whole retention policy passed the filter (1.095 to 1.187) although it says nothing on these questions, and it is past review.
- **v1-01, v1-21, conflict-02** (tier): BRONZE expected, SILVER returned. The answers give 72 hours, but the draft policy stayed above the cutoff (1.344, 1.305, 1.255). v1-21's SILVER is what the README describes for this question; under the frozen labels it is a miss, because the draft makes a claim about breach deadlines.

## Why these numbers differ from the README

The README reports 95% (19 of 20) for article chunking. That figure came from the v1 harness, which:

- sent the top 3 chunks to the model with **no relevance filter**. `/query` keeps only chunks under 1.2, so ten answerable questions here never reach the model;
- was **scored by hand**, on whether the answer or the refusal was right; tiers were never scored;
- used 20 questions. v2 adds 25, including casual paraphrases, whose embeddings land further from the text.

The model (`gemini-3.6-flash`) and the three documents are the same, so the gap comes from the pipeline, the scoring and the questions. The closest like-for-like figure: on v1's 20 questions the system now refuses correctly 17 times, with 3 false refusals and 0 false answers, against the README's 1 false refusal and 0 false answers. Two of the extra false refusals (v1-04, v1-07) are questions whose closest chunk is above the cutoff the v1 harness never applied.

## Limitations of this baseline

- One person wrote every question and label, and 48 questions is small: one question moves the overall figures by about 2 points.
- Facts are matched as phrases. A wrong answer that contains the phrase passes ("not 72 hours"); a correct one worded differently fails.
- The model is not deterministic. Answers are cached on one machine only (the cache is gitignored), so a fresh run may move a question or two; the results file is the record.
- The 1.2 cutoff dominates these numbers, and it was chosen by inspection (→ P3.2 sweeps it).

## What should move these numbers

- **P0.4**, the refusal sentinel: changes how a refusal is recognised, not what retrieval finds.
- **P0.5**, structure-aware chunking: splits Art. 12 by paragraph and each policy by section, aimed at the Art. 12 misses and the whole-policy tier changes.
- **P2.4**: conflict scoring, against this baseline's zero.
- **P3.2**: the retrieval-only cutoff sweep.

Each is measured on these 48 questions and compared with this file, group by group.
