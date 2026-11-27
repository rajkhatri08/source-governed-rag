"""Run evaluation v2 through the same code path as /query, against the eval database.

Run from the repo root: python -m evals.run_eval --label baseline
Prints a table and writes evals/results/<run date>-<label>.json. Model answers are cached in
evals/cache/ (gitignored), so re-running an unchanged configuration makes no model calls.
"""
import argparse
import json
import os
import statistics
import subprocess
import time
from datetime import date
from pathlib import Path

from google import genai
from google.genai import errors

from evals.eval_db import use_eval_database
from evals.questions_v2 import QUESTIONS
from evals.scoring import METRICS, cache_key, score_question, sha256_text, summarise
from generate import GENERATION_CONFIG, MODEL, PROMPT_TEMPLATE, generate_answer, new_client
from govern import RELEVANCE_CUTOFF
from retrieve import new_index, search

EVAL_HOST = use_eval_database()  # before the two imports below: db.py reads DATABASE_URL on import

from db import read_documents_to_index  # noqa: E402
from pipeline import TOP_K, ModelUnavailable, answer_question, index_documents, ms_since  # noqa: E402

EVAL_TODAY = date(2026, 11, 18)  # fixed, so results don't change by themselves when a review date passes
PAUSE_SECONDS = 2  # between model calls (paid tier)
RETRY_WAITS = [15, 30, 60, 120]  # exponential backoff on a 429 or 503, in seconds
RETRY_CODES = (429, 503)
CACHE_DIR = Path("evals/cache")
RESULTS_DIR = Path("evals/results")
QUESTIONS_FILE = Path("evals/questions_v2.py")


def call_with_retry(client: genai.Client, question: str, chunk_texts: list[str]) -> str:
    """Call the model; on a 429 or 503 wait (15, 30, 60, 120 s) and try again, then give up."""
    attempt = 0
    while True:
        try:
            return generate_answer(client, question, chunk_texts)
        except errors.APIError as e:
            if e.code not in RETRY_CODES or attempt == len(RETRY_WAITS):
                raise
            print(f"    model returned {e.code}; retrying in {RETRY_WAITS[attempt]} s")
            time.sleep(RETRY_WAITS[attempt])
            attempt += 1


class CachedGenerator:
    """Model calls through the on-disk cache, keyed on everything that can change an answer."""

    def __init__(self, collection):
        everything = collection.get()
        self.id_by_text = dict(zip(everything["documents"], everything["ids"]))
        self.calls = 0
        self.hits = 0
        # How long each real model call took. Timed here, not taken from answer_question's timings_ms, which in
        # the eval include the pause below and the cache lookups.
        self.call_ms = []

    def generate(self, client: genai.Client, question: str, chunk_texts: list[str]) -> str:
        """Same signature as generate_answer: the cached answer, or a model call that is then cached."""
        # answer_question passes chunk texts only, so each chunk's id is looked up from its text.
        chunk_ids = [self.id_by_text[text] for text in chunk_texts]
        key = cache_key(question, chunk_ids, chunk_texts, PROMPT_TEMPLATE, MODEL, GENERATION_CONFIG)
        path = CACHE_DIR / f"{key}.json"
        if path.exists():
            self.hits += 1
            return json.loads(path.read_text())["answer"]
        started = time.perf_counter()
        answer = call_with_retry(client, question, chunk_texts)
        call_ms = ms_since(started)
        self.calls += 1
        self.call_ms.append(call_ms)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"question": question, "chunk_ids": chunk_ids, "model": MODEL,
                                    "generation_config": GENERATION_CONFIG, "answer": answer,
                                    "model_call_ms": call_ms}, indent=1))
        time.sleep(PAUSE_SECONDS)
        return answer


def passing_chunks(collection, result: dict | None) -> list[dict]:
    """The chunks that passed the relevance filter, in rank order, with their source and text."""
    if result is None or not result["sources"]:
        return []
    got = collection.get(ids=result["sources"])
    text_by_id = dict(zip(got["ids"], got["documents"]))
    source_by_id = {chunk_id: meta["source"] for chunk_id, meta in zip(got["ids"], got["metadatas"])}
    return [{"id": chunk_id, "source": source_by_id[chunk_id], "distance": distance,
             "text": text_by_id[chunk_id]}
            for chunk_id, distance in zip(result["sources"], result["distances"])]


def retrieved_before_filter(collection, question: str) -> list[dict]:
    """Diagnostic only, not scored: the top chunks before the relevance filter, to explain early returns."""
    texts, ids, distances, metas = search(collection, question, TOP_K)
    return [{"id": chunk_id, "source": meta["source"], "distance": distance, "first_line": text.split("\n")[0]}
            for text, chunk_id, distance, meta in zip(texts, ids, distances, metas)]


def run_questions(questions: list[dict], collection, client, generate) -> list[dict]:
    """Every question through answer_question, scored. A failed model call is recorded, not dropped."""
    rows = []
    for number, question in enumerate(questions, start=1):
        print(f"[{number:2}/{len(questions)}] {question['id']}")
        error = None
        try:
            result = answer_question(question["question"], collection, client,
                                     today=EVAL_TODAY, generate=generate)
        except ModelUnavailable as e:
            # The model's own outage, after any retries. Anything else, a bug included, isn't ModelUnavailable and
            # stops the run. A timeout or a lost connection has no HTTP code.
            result = None
            error = {"code": getattr(e.__cause__, "code", None), "message": f"{e}: {e.__cause__}"}
        chunks = passing_chunks(collection, result)
        rows.append({
            **question,
            "result": result,
            "error": error,
            "scores": score_question(question, result, chunks),
            "passing_chunks": [{"id": c["id"], "source": c["source"], "distance": c["distance"],
                                "first_line": c["text"].split("\n")[0]} for c in chunks],
            "retrieved_before_filter": retrieved_before_filter(collection, question["question"]),
        })
    return rows


def print_table(summary: dict) -> None:
    """One row per group, overall last: each metric as correct/n, then the failed questions."""
    names = [name for name in summary if name != "overall"] + ["overall"]
    print(f"\n{'group':<24}{'n':>4}" + "".join(f"{metric:>10}" for metric in METRICS) + f"{'failed':>8}")
    for name in names:
        counts = summary[name]
        cells = ""
        for metric in METRICS:
            cell = counts[metric]
            cells += (f"{cell['correct']}/{cell['n']}" if cell["n"] else "-").rjust(10)
        print(f"{name:<24}{counts['questions']:>4}{cells}{counts['failed']:>8}")


def git_state() -> dict:
    """The commit the run used, and whether the working tree had uncommitted changes."""
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout
    return {"commit": commit, "uncommitted_changes": bool(status.strip())}


def corpus_sha256(collection) -> str:
    """One hash over every indexed chunk's id and text, to tell whether two runs saw the same corpus."""
    everything = collection.get()
    pairs = sorted([chunk_id, sha256_text(text)]
                   for chunk_id, text in zip(everything["ids"], everything["documents"]))
    return sha256_text(json.dumps(pairs))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run evaluation v2 against the eval database.")
    parser.add_argument("--label", required=True, help="a name for this run, e.g. baseline")
    args = parser.parse_args()

    run_date = date.today().isoformat()
    results_path = RESULTS_DIR / f"{run_date}-{args.label}.json"
    if results_path.exists():
        raise SystemExit(f"{results_path} already exists: pick another label")

    print(f"eval database host: {EVAL_HOST}")
    collection = new_index()
    indexed, failed = index_documents(collection, read_documents_to_index())
    if failed:
        raise SystemExit(f"eval documents could not be indexed: {failed}")

    client = new_client(os.getenv("GOOGLE_API_KEY"))
    generator = CachedGenerator(collection)
    rows = run_questions(QUESTIONS, collection, client, generator.generate)
    summary = summarise(rows)
    print_table(summary)

    run = {
        "label": args.label,
        "run_date": run_date,
        "eval_today": EVAL_TODAY.isoformat(),
        "model": MODEL,
        "generation_config": GENERATION_CONFIG,
        "prompt_template_sha256": sha256_text(PROMPT_TEMPLATE),
        "top_k": TOP_K,
        "relevance_cutoff": RELEVANCE_CUTOFF,
        "git": git_state(),
        "questions_sha256": sha256_text(QUESTIONS_FILE.read_text()),
        "corpus_sha256": corpus_sha256(collection),
        "chunks_indexed": collection.count(),
        "model_calls": generator.calls,
        "cache_hits": generator.hits,
        "model_call_ms": generator.call_ms,  # each real call's time, in the order made; retry waits included
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    results_path.write_text(json.dumps({"run": run, "summary": summary, "rows": rows}, indent=1))
    print(f"\nwrote {results_path} ({generator.calls} model calls, {generator.hits} answers from the cache)")
    if generator.call_ms:
        print(f"model call: median {statistics.median(generator.call_ms):.0f} ms, "
              f"slowest {max(generator.call_ms)} ms")


if __name__ == "__main__":
    main()
