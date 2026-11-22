# P0.3b: real-world PDFs, measured

**How these numbers were made.**
- **The test set.** Seven PDFs, listed with their sources in [`evals/fixtures/pdfs/README.md`](../../evals/fixtures/pdfs/README.md).
- **The questions.** Two for each accepted PDF, written from its extracted text before any was asked ([`evals/pdf_questions.py`](../../evals/pdf_questions.py)). Each is answered through `answer_question`, as `/query` would, on an index holding only that PDF.
- **The runs.** `MAX_UPLOAD_CHUNKS=2000 python -m evals.pdf_robustness`, on 22 Nov 2026 ([results](../../evals/results/2026-11-22-pdf-robustness.json)) and on 21 Nov ([results](../../evals/results/2026-11-21-pdf-robustness.json)). The 21 Nov run used the same pipeline, but before three changes:
  - hyphenated words were rejoined
  - the furniture rule counted odd and even pages separately
  - the chunker capped its pieces

  The limit was raised so that the 144-page regulation could be measured too; at the default of 300 it is refused.
- **Memory and timings.** Measured on an 8-core MacBook, through the real server against a local scratch database. Resident memory (RSS) on macOS is not what Linux would report, so read the memory figures as comparisons, not as a prediction of Render's numbers.

## Summary

- **Every PDF ended in a clear outcome.**
  - The scan and the password-protected PDF were refused with a message saying why.
  - The five public PDFs were accepted.
  - The 144-page EU AI Act is refused at the default limit with "the text is too long to index: it splits into 708 chunks, more than the 300-chunk limit: split it into smaller files".
- **10 of 10 questions were answered correctly.** On 21 Nov it was 5 of the 6 then asked; the miss was a table row, fixed by rejoining hyphenated words.
- **The test set found six problems that no unit test had.** All are fixed and measured below:
  - page breaks splitting sentences
  - words hyphenated at line ends
  - a footer the furniture rule missed
  - a regulation that collapsed into a single chunk
  - the embedding model being reloaded on every call
  - batch memory that let one upload and one question exceed 512 MB
- **Embedding is the cost.** It takes about 0.2 CPU-seconds per chunk on this Mac; extraction is fast. On Render, timed at Ship 1 (22 Nov), a whole upload takes about 3.0 s per chunk, against an estimate of 2 s: BERT's 71 chunks took 3 min 34 s. So Render runs with a 100-chunk limit, about 5 minutes at most; the code's default stays 300 for local use.
- **Peak memory** for a 300-chunk upload with a question arriving during it is 280 MB, down from 1,066 MB.
- **A cold start reads stored vectors.** The index of 736 chunks builds in 0.6 s from stored vectors, against 53.1 s re-embedding (183 CPU-seconds, which would be several minutes on Render).

## The test set

| PDF | What it tests | Outcome | Pages | Chunks | Extract | Embed (CPU) |
|---|---|---|---|---|---|---|
| `scanned_policy.pdf` | a scan | 422 "no extractable text: this PDF has no text layer (is it a scan?)" | | | | |
| `protected_policy.pdf` | a password | 422 "this PDF is password-protected: remove the password and upload it again" | | | | |
| `apple_privacy_policy.pdf` | a company policy; one page | accepted | 1 | 38 | 0.3 s | 2.0 s (7.7 s) |
| `bert_two_column.pdf` | two columns | accepted | 16 | 71 | 0.5 s | 3.3 s (13.3 s) |
| `edps_breaches_factsheet.pdf` | a designed factsheet | accepted | 6 | 7 | 0.4 s | 0.2 s (1.0 s) |
| `nist_ai_rmf_1.0.pdf` | tables | accepted | 48 | 129 | 0.7 s | 9.1 s (32.7 s) |
| `eu_ai_act.pdf` | a long regulation | 413 at the default limit; accepted at 2,000 | 144 | 708 | 3.4 s | 37.4 s (147.2 s) |

No short one-page public PDF could be downloaded automatically, so the one-page case is Apple's single long page.

## What extraction changes

"Before P0.3b" means extraction as it was, with no furniture removal, a blank line at every page break, and no rejoined words. Today's chunker runs on both sides, so these columns isolate extraction.

| PDF | Furniture dropped | Page breaks joined | Words rejoined | Chunks now (before P0.3b) |
|---|---|---|---|---|
| Apple | none (one page) | 0 of 0 | 0 | 38 (38) |
| BERT | none: the arXiv version has no running headers | 5 of 15 | 323 | 71 (74) |
| EDPS | none | 0 of 5 | 0 | 7 (7) |
| NIST | `NIST AI #-# AI RMF #.#` (the header), `Page #` | 6 of 47 | 237 | 129 (131) |
| EU AI Act | `OJ L, #.#.# EN` and `EN OJ L, #.#.#`, `ELI: http://data.europa.eu/eli/reg/#/#/oj #/#` and `#/# ELI: http://data.europa.eu/eli/reg/#/#/oj` | 13 of 143 | 1 | 708 (714) |

A `#` stands for any run of digits. Every pattern dropped is a header or footer; no line of body text was dropped.

## Six problems the test set found

1. **Page breaks split sentences.**
   - **The cause.** pypdf ends each page's text with a line break, so joining pages with another one left a blank line between them, which the cleaner treats as a paragraph break. "…which include ‖ valid and reliable, safe…" stayed in two pieces, in 6 of NIST's 47 page breaks and 5 of BERT's 15.
   - **The fix.** Pages are joined by a single line break, so a page break is handled exactly like a line break within a page.
   - **The side effect.** The arXiv stamp beside BERT's first page is now glued to page 2's first line, in one chunk. That's the same guess the line-joining rule already makes within a page.
2. **Words hyphenated at line ends stayed broken:** "recommenda- tion", "re- sponsibility", 237 times in NIST and 323 in BERT.
   - **The fix.** A lower-case letter, a hyphen, a line break and a lower-case letter are joined. "AI-\nbased", "2019-\n2020" and a dash after a space are left alone.
   - **The cost.** A compound broken at its own hyphen loses it: "pre-\ntrain" becomes "pretrain".
   - **The effect.** On the six questions asked both days, correct answers went from 5 to 6 (table below).
3. **The Official Journal's footer was missed.**
   - **The cause.** It alternates between odd and even pages ("ELI: … 5/144" and "6/144 ELI: …"), and its footnotes push it off the last line of some pages. Each version was the last line on about 60 of 144 pages, 42%, under the "at least half" rule.
   - **The fix.** Odd and even pages are also counted on their own, and a pattern must be on at least two pages. The rule now drops all four of the Journal's header and footer patterns, about 8,800 characters.
   - **The check.** The only line that comes close to looking like furniture, "Article N", starts 14 of the 144 pages: kept.
4. **The chunker made the 144-page regulation one chunk** of 595,000 characters. That's a silent failure: the model embeds only a chunk's first 256 tokens, so the regulation was all but unsearchable. It even passed a 300-chunk limit.
   - **The cause.** A cross-reference had wrapped so that a line began "Section 2. The EU declaration of conformity…". The chunker took it for the only section heading, and made the 385,000 characters before it a "title" prefixed to that one section.
   - **The fix.**
     - The lines before the first heading are a title only if they are at most 300 characters; longer, they are packed as content.
     - No chunk is longer than 1,500 characters; a longer piece is packed line by line.
     - The longest title prefix in the seed documents is 119 characters and their longest chunk 1,391, so the eval corpus is unchanged: its 26 chunk ids are the same.
5. **The embedding model was reloaded on every call.**
   - **The cause.** Chroma's default embedder creates a new model, about 170 MB, for each call: each question, each upload. An upload and a question at the same moment held two.
   - **The fix.** One instance (`retrieve.EMBEDDER`), loaded at startup, serves startup, uploads and questions alike.
   - **The detail.** Given this model, Chroma would choose cosine distance, so the index's L2 distance is pinned explicitly.
   - **The check.** All 48 eval questions return the same top 5 with bit-identical distances.
6. **Batches of 32 set the memory peak.**
   - **The cause.** The model pads every text to 256 tokens, so a call's working memory grows with the number of texts in it, and ONNX Runtime keeps that memory reserved afterwards.
   - **The fix.** Chunks are embedded one per call. That was the least memory and the least CPU of the five sizes tried, and the vectors are bit-identical at every size.

## Questions

| Question | PDF, where | Correct | Distance now (before P0.3b) |
|---|---|---|---|
| pdf-apple-1: below what age is a person a child? | Apple, Children and Minors | yes: "under the age of 13" | 0.987 (0.987) |
| pdf-apple-2: which company controls EEA personal data? | Apple, Apple Companies Around the World | yes: Apple Distribution International, Ireland | 0.461 (0.461) |
| pdf-bert-1: layers and parameters of BERT-LARGE | BERT, Section 3 | yes: 24 layers, 340M | 0.709 (0.653) |
| pdf-bert-2: how often is a chosen word replaced with [MASK]? | BERT, Appendix A.1 | yes: 80% | 0.453 (0.612) |
| pdf-edps-1: how quickly must an EU institution notify the EDPS? | EDPS, what to do | yes: no later than 72 hours | 0.366 (0.366) |
| pdf-edps-2: which types of breach? | EDPS, types of breaches | yes: confidentiality, availability, integrity | 0.551 (0.551) |
| pdf-aiact-1: whose AI literacy must be ensured? | EU AI Act, Article 4, the middle of the text (45%) | yes: their staff and others acting on their behalf | 0.399 (0.399) |
| pdf-aiact-2: within how many days must a serious incident be reported? | EU AI Act, Article 73(2) | yes: 15 days | 0.459 (0.469) |
| pdf-rmf-1: the four functions of the AI RMF Core | NIST, Part 2 | yes: govern, map, measure, manage | 0.532 (0.472) |
| pdf-rmf-2: who takes responsibility for decisions about AI risks? | NIST, Table 1, GOVERN 2.3: a table row | yes: executive leadership | 0.544 (0.627, and the row not retrieved) |

Lower distance means a closer match; only chunks under 1.2 reach the model, and only the best 5.
- **The four questions also asked on 21 Nov,** before words were rejoined, moved as follows:
  - BERT 0.653 → 0.709 and 0.612 → 0.453
  - NIST 0.497 → 0.532 and 0.581 → 0.544
  - Chunk boundaries shift when words rejoin, so distances move both ways.
- **pdf-rmf-2 was the one wrong answer on 21 Nov.** The model said it didn't know, because the chunk holding the GOVERN 2.3 row ranked 14th of 129 (distance 0.754): inside the cutoff, but outside the top 5. Tables come out of a PDF as lines that get packed together, so a row shares its chunk, and its vector, with its neighbouring rows ("re- sponsibility" didn't help). Since the rejoining, the row is retrieved and answered.
- **Tables are still flattened,** and a single table row can still lose to prose on the same topic.

## Cost and limits

**Embedding takes 137–254 CPU-milliseconds per chunk** on this Mac, about 0.2 on average. Extraction is fast: 3.4 s for the 144 pages of the EU AI Act.

**On Render, timed at Ship 1 (22 Nov).** Uploading `bert_two_column.pdf` (16 pages, 71 chunks) to the free instance took 3 min 34 s for the whole request: transfer, extraction, embedding and the database write. That is about 3.0 s per chunk, 1.5 times the 2 s estimated from this Mac (a tenth of a CPU, so ten times the CPU-seconds). The other rows assume the time grows in proportion to the chunks, at 3.0 s each:

| Upload | Chunks | On Render |
|---|---|---|
| `bert_two_column.pdf` | 71 | 3 min 34 s, measured |
| a document at Render's limit | 100 | about 5 minutes |
| `nist_ai_rmf_1.0.pdf` | 129 | about 6½ minutes; measured: refused at Render's limit within seconds |
| a document at the code's default limit | 300 | about 15 minutes |
| `eu_ai_act.pdf` | 708 | about 35 minutes |

The upload finished without an out-of-memory restart, but Render's free tier shows no memory or CPU graph, so the peak there is unmeasured; P0.4 logs each upload's chunk count, seconds and peak memory. The time covers the whole request, so it doesn't separate embedding from the other steps either.

**The limits:**
- 10 MB per file (413)
- 300 pages (413), checked before any text is extracted
- `MAX_UPLOAD_CHUNKS` chunks (413), 300 when unset: about 300,000 characters. Render sets 100.

The page limit rarely binds, because the chunk limit refuses long documents first. The chunk limit can be raised locally (the EU AI Act needs 708); on Render it was lowered to 100 once an upload had been timed there.

**Texts per model call, embedding the same 300 chunks.** Each size ran in a fresh process, measured after the model was loaded. The vectors were bit-identical at every size.

| Texts per call | Peak memory above the loaded model | Seconds | CPU-seconds |
|---|---|---|---|
| 32 (Chroma's default) | +231 MB | 22.1 | 80.3 |
| 16 | +197 MB | 19.1 | 73.8 |
| 8 | +82 MB | 19.6 | 73.4 |
| 4 | +19 MB | 18.5 | 71.4 |
| **1 (shipped)** | **+0 MB** | **16.6** | **64.4** |

## Memory

**Through the real server.** Each row is a separate run.

| Configuration | Corpus | Ready | Question alone | Upload with a question during it |
|---|---|---|---|---|
| A new model per call, batches of 32 (before) | 4,157 chunks | 452 MB | 458 MB peak, 0.139 s | 1,066 MB peak (2,000 chunks) |
| One shared model, batches of 32 | 4,157 chunks | 493 MB | 498 MB, 0.063 s | 787 MB (2,000 chunks); 814 MB (300 chunks) |
| **One shared model, one chunk per call (shipped)** | **28 chunks** | **287 MB** | **296 MB, 0.087 s** | **280 MB (300 chunks)** |
| shipped; the EU AI Act uploaded with the limit raised | 28 → 736 chunks | 287 → 384 MB | | 392 MB peak (708 chunks, 49.4 s) |

- **The memory a question needs.** With batches of 32, a question cost the same memory whether a 300- or a 2,000-chunk upload was running. That's why lowering the chunk limit wasn't the answer to memory: the batch size was.
- **At the shipped default,** an upload with a question during it stays within the memory the server holds anyway.
- **Larger documents** add memory for their parsing and their rows: 392 MB for 708 chunks.
- **What the index costs at rest isn't pinned down.** Ready memory rose by 97 MB with the EU AI Act's 708 chunks (287 → 384 MB). In an earlier run it rose by 161 MB with 4,129 synthetic chunks (359 → 520 MB). macOS's figures give no reliable cost per chunk; what is certain is that the index grows with every upload, and there is no limit on the total.
- **Render.** At Ship 1 the 71-chunk upload finished without an out-of-memory restart. Render's free tier shows no memory graph, so the peak there is unmeasured until P0.4 logs it.

## Startup

| Index build | From stored vectors | Re-embedding every chunk (as before P0.3b) |
|---|---|---|
| 736 chunks (the seeds and the EU AI Act), shipped code | 0.6 s | 53.1 s (183 CPU-seconds) |
| 4,157 chunks (with two synthetic 2,000-chunk documents), the earlier embedder | 2.1 s | 233.5 s (921.5 CPU-seconds) |

- **Ready times.** The server was ready in 2.4 s with 28 chunks, 3.4 s with 736 and 4.6 s with 4,157. Every start used the stored vectors (no "re-embedding" warning), and the model loads first.
- **What re-embedding would cost on Render.** Re-embedding the 736 chunks takes 183 CPU-seconds here, which would be roughly half an hour on a tenth of a CPU. Reading their stored vectors takes under a second.
- **No background indexing needed.** The task's fallback (bind the port first, index in the background) isn't needed while startup takes seconds.

## What this doesn't show

- **Render.** Its speed comes from one timed upload (the whole request, not embedding alone), and its memory isn't measured: the free tier has no memory graph.
- **Coverage.** Ten questions on five PDFs, written by one person: enough to catch the failures above, not to claim an accuracy rate on arbitrary PDFs.
- **Scans.** A scanned PDF is refused, not read: there is no OCR.
- **Tables and two-column layouts** come through as lines, in pypdf's reading order.
- **The extraction rules are heuristics:**
  - a furniture line can be missed (for example a header pypdf places mid-page)
  - a sentence that continues on a page starting with a capital letter is not joined
  - a compound broken at its own hyphen loses the hyphen
