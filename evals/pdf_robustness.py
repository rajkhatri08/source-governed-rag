"""P0.3b: real-world PDFs through the upload checks and the question pipeline, measured.

Run from the repo root:
    python -m evals.pdf_robustness --extract-only    the upload checks and embedding only: no model, no database
    python -m evals.pdf_robustness                   then each accepted PDF's questions (evals/pdf_questions.py)

Reads every PDF in evals/fixtures/pdfs/ and evals/fixtures/pdfs/local/ (gitignored: see the README there).
For each one: accepted or rejected with the message, pages, chunks now and before P0.3b, the furniture
dropped, the page breaks joined, the hyphenated words rejoined, and the seconds (wall and CPU) to
extract, embed and index it.
Then each accepted PDF's questions go through answer_question, as /query would, on an index holding
only that PDF. It is unapproved, so every answer is BRONZE: only the facts are scored. The same
questions are also retrieved, without the model, from the chunks extraction gave before P0.3b.
Model answers go through the eval's cache; audit rows go to the eval database, as in run_eval.
The upload limits apply as on the server: MAX_UPLOAD_CHUNKS=2000 measures longer documents too.
Writes evals/results/<date>-pdf-robustness.json (or -pdf-extraction.json with --extract-only).
"""
import argparse
import hashlib
import json
import os
import re
import time
from datetime import date
from pathlib import Path

from evals.run_eval import EVAL_TODAY, RESULTS_DIR, CachedGenerator  # first: points DATABASE_URL at the eval database
from evals.pdf_questions import PDF_QUESTIONS
from evals.scoring import missing_facts, was_refused
from generate import new_client
from govern import RELEVANCE_CUTOFF
from ingest import BROKEN_WORD, chunk_document, clean_pdf_text, clean_text
from pipeline import TOP_K, answer_question, index_document
from retrieve import embed, new_index, search
from upload import MAX_CHUNKS, UploadRejected, drop_page_furniture, page_furniture, pdf_pages, prepare_upload

FIXTURES = Path("evals/fixtures/pdfs")
FIELDS = {"owner": "P0.3b test set", "version": "1", "review_date": "2027-12-31"}


def timed(work):
    """Run work(); return its result, wall seconds and CPU seconds (all of this process's threads)."""
    wall, cpu = time.perf_counter(), time.process_time()
    result = work()
    return result, round(time.perf_counter() - wall, 2), round(time.process_time() - cpu, 2)


def page_breaks_joined(pages: list[str]) -> int:
    """Page breaks that clean_pdf_text joins: a page ends without a full stop and the next goes on in lower case."""
    pairs = [(page.strip(), following.strip()) for page, following in zip(pages, pages[1:])]
    return sum(1 for page, following in pairs
               if page and following and not page.endswith(".") and not re.match(r"[A-Z0-9(]", following))


def extraction(path: Path) -> tuple[dict, list[str], list[str]]:
    """The upload checks on one PDF, measured. Returns the row, its chunks now, and its chunks before P0.3b."""
    data = path.read_bytes()
    row = {"file": path.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    try:
        prepared, row["extract_seconds"], row["extract_cpu_seconds"] = timed(
            lambda: prepare_upload(path.name, data, **FIELDS))
    except UploadRejected as e:
        return {**row, "accepted": False, "status": e.status, "message": e.message}, [], []
    pages = pdf_pages(data)
    kept = drop_page_furniture(pages)
    before = chunk_document(clean_text(clean_pdf_text("\n".join(pages))))  # what extract_text gave before P0.3b
    furniture_only = chunk_document(clean_text(clean_pdf_text("\n".join(kept))))  # furniture dropped, old page join
    row.update(accepted=True, pages=len(pages), characters=len(prepared.content), chunks=len(prepared.chunks),
               chunks_before_p03b=len(before), chunks_with_only_furniture_removal=len(furniture_only),
               furniture=sorted(page_furniture(pages)), page_breaks=max(len(pages) - 1, 0),
               page_breaks_joined=page_breaks_joined(kept),
               words_rejoined=len(BROKEN_WORD.findall("\n".join(page.strip() for page in kept))))
    return row, prepared.chunks, before


def build_index(path: Path, chunks: list[str], vectors: list[list[float]]):
    """A fresh index holding only this PDF, unapproved, as an upload would be."""
    collection = new_index()  # drops the previous PDF's
    doc = {"filename": path.name, "approved": False, **FIELDS}
    index_document(collection, doc, chunks, vectors)
    return collection


def retrieval(collection, question: dict) -> dict:
    """Without the model: the best distance, and whether the chunks passing the cutoff hold the expected facts."""
    texts, ids, distances, metas = search(collection, question["question"], TOP_K)
    passing = [text for text, distance in zip(texts, distances) if distance < RELEVANCE_CUTOFF]
    return {"top_distance": round(distances[0], 3) if distances else None, "passing_chunks": len(passing),
            "facts_retrieved": not missing_facts("\n".join(passing), question["expected_facts"])}


def ask(path: Path, chunks: list[str], vectors: list[list[float]], before: list[str], client) -> list[dict]:
    """Each of this PDF's questions: answered now, and retrieved from the chunks before P0.3b."""
    questions = PDF_QUESTIONS.get(path.name, [])
    collection = build_index(path, chunks, vectors)
    generator = CachedGenerator(collection)
    rows = []
    for question in questions:
        result = answer_question(question["question"], collection, client, today=EVAL_TODAY,
                                 generate=generator.generate)
        missing = missing_facts(result["answer"], question["expected_facts"])
        rows.append({**question, "answer": result["answer"], "refused": was_refused(result),
                     "missing_facts": missing, "correct": not was_refused(result) and not missing,
                     "now": retrieval(collection, question)})
    collection = build_index(path, before, embed(before))
    for row in rows:
        row["before_p03b"] = retrieval(collection, row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure the P0.3b PDF test set.")
    parser.add_argument("--extract-only", action="store_true", help="no questions: no model, no database")
    args = parser.parse_args()

    client = None if args.extract_only else new_client(os.getenv("GOOGLE_API_KEY"))
    paths = sorted(FIXTURES.glob("*.pdf")) + sorted((FIXTURES / "local").glob("*.pdf"))
    results = []
    for path in paths:
        row, chunks, before = extraction(path)
        if row["accepted"]:
            vectors, row["embed_seconds"], row["embed_cpu_seconds"] = timed(lambda: embed(chunks))
            _, row["index_seconds"], _ = timed(lambda: build_index(path, chunks, vectors))
            if client is not None:
                row["questions"] = ask(path, chunks, vectors, before, client)
            print(f"{path.name}: {row['pages']} pages, {row['chunks']} chunks (before P0.3b {row['chunks_before_p03b']}), "
                  f"furniture {row['furniture']}, page breaks joined {row['page_breaks_joined']}/{row['page_breaks']}, "
                  f"extract {row['extract_seconds']} s, embed {row['embed_seconds']} s")
            for q in row.get("questions", []):
                print(f"    {q['id']}: correct {q['correct']} (missing {q['missing_facts']}); facts retrieved now "
                      f"{q['now']['facts_retrieved']}, before P0.3b {q['before_p03b']['facts_retrieved']}")
        else:
            print(f"{path.name}: rejected {row['status']}: {row['message']}")
        results.append(row)

    label = "pdf-extraction" if args.extract_only else "pdf-robustness"
    results_path = RESULTS_DIR / f"{date.today().isoformat()}-{label}.json"
    results_path.write_text(json.dumps({"run_date": date.today().isoformat(), "max_chunks": MAX_CHUNKS,
                                        "pdfs": results}, indent=1))
    print(f"\nwrote {results_path}")


if __name__ == "__main__":
    main()
