"""Pure scoring functions for the evaluation: no database, no network."""
import hashlib
import json

from govern import is_refusal

METRICS = ["refusal", "facts", "tier", "sources"]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cache_key(question: str, chunk_ids: list[str], chunk_texts: list[str],
              prompt_template: str, model: str) -> str:
    """One hash over everything that can change the model's answer."""
    payload = {
        "question": question,
        "chunks": [[chunk_id, sha256_text(text)] for chunk_id, text in zip(chunk_ids, chunk_texts)],
        "prompt_template_sha256": sha256_text(prompt_template),
        "model": model,
    }
    return sha256_text(json.dumps(payload, sort_keys=True))


def was_refused(result: dict) -> bool:
    """True if no chunk passed the relevance filter, or the model said it couldn't answer."""
    return len(result["sources"]) == 0 or is_refusal(result["answer"])


def normalise(text: str) -> str:
    """Lower case, with runs of whitespace collapsed to one space."""
    return " ".join(text.lower().split())


def missing_facts(answer: str, expected_facts: list[list[str]]) -> list[list[str]]:
    """The fact lists none of whose alternatives appear in the answer."""
    text = normalise(answer)
    return [alternatives for alternatives in expected_facts
            if not any(normalise(alternative) in text for alternative in alternatives)]


def missing_sources(passing_chunks: list[dict], expected_sources: list) -> list[list[str]]:
    """The expected (document, heading) pairs that no passing chunk matches."""
    return [[document, heading] for document, heading in expected_sources
            if not any(chunk["source"] == document and heading in chunk["text"]
                       for chunk in passing_chunks)]


def score_question(question: dict, result: dict | None, passing_chunks: list[dict]) -> dict:
    """Each metric is True, False, or None where it doesn't apply. A failed model call (no
    result) scores nothing: it is counted as failed, never as wrong."""
    if result is None:
        return {"failed": True, "refused": None, "refusal": None, "facts": None, "tier": None,
                "sources": None, "missing_facts": [], "missing_sources": []}
    refused = was_refused(result)
    facts_missing = missing_facts(result["answer"], question["expected_facts"])
    sources_missing = missing_sources(passing_chunks, question["expected_sources"])
    return {
        "failed": False,
        "refused": refused,
        "refusal": refused == (not question["answerable"]),
        "facts": not facts_missing if question["expected_facts"] else None,
        "tier": result["tier"] == question["expected_tier"],
        "sources": not sources_missing if question["expected_sources"] else None,
        "missing_facts": facts_missing,
        "missing_sources": sources_missing,
    }


def applies(question: dict, metric: str) -> bool:
    """Whether a metric is scored for a question, decided by its labels alone."""
    if metric == "facts":
        return bool(question["expected_facts"])
    if metric == "sources":
        return bool(question["expected_sources"])
    return True


def empty_counts() -> dict:
    counts = {"questions": 0, "failed": 0}
    for metric in METRICS:
        counts[metric] = {"correct": 0, "wrong": 0, "failed": 0, "n": 0}
    return counts


def summarise(rows: list[dict]) -> dict:
    """Counts per group and overall. Each metric's n comes from the labels, so the denominator
    never depends on the run: a failed question stays in it, counted as failed."""
    summary = {}
    for row in rows:
        for name in (row["group"], "overall"):
            counts = summary.setdefault(name, empty_counts())
            counts["questions"] += 1
            if row["scores"]["failed"]:
                counts["failed"] += 1
            for metric in METRICS:
                if not applies(row, metric):
                    continue
                cell = counts[metric]
                cell["n"] += 1
                if row["scores"]["failed"]:
                    cell["failed"] += 1
                elif row["scores"][metric]:
                    cell["correct"] += 1
                else:
                    cell["wrong"] += 1
    return summary
