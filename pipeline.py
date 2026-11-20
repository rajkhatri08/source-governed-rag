"""The question-answering pipeline, shared by the API and the evaluation."""
import logging
from collections.abc import Callable
from datetime import date

from chromadb import Collection
from google import genai
from pypdf.errors import PyPdfError

from db import log_query_db
from generate import generate_answer
from govern import RELEVANCE_CUTOFF, classify, is_expired
from ingest import load_document, load_pdf, clean_text, clean_pdf_text, chunk_by_article
from retrieve import build_index, search

logger = logging.getLogger(__name__)

TOP_K = 5


class ModelUnavailable(Exception):
    """Generating the answer failed; the API reports it as 503."""


def index_documents(docs: list[dict]) -> tuple[Collection, list[str]]:
    """Chunk and index every active document, skipping any whose file is missing or unreadable.

    Returns the collection and the filenames that were skipped.
    """
    all_chunks = []
    all_meta = []
    skipped = []

    for d in docs:
        if d["retired"]:
            continue
        try:
            if d["filename"].endswith(".pdf"):
                text = clean_pdf_text(load_pdf("documents/" + d["filename"]))
            else:
                text = load_document("documents/" + d["filename"])
            cleaned = clean_text(text)
            doc_chunks = chunk_by_article(cleaned)
        except (OSError, UnicodeDecodeError, PyPdfError) as e:
            logger.warning(f"skipping {d['filename']} at startup: {type(e).__name__}: {e}")
            skipped.append(d["filename"])
            continue
        for chunk in doc_chunks:
            chunk_meta = {
                "source": d["filename"],
                "owner": d["owner"],
                "version": d["version"],
                "approved": d["approved"],
                "review_date": d["review_date"]
            }
            all_chunks.append(chunk)
            all_meta.append(chunk_meta)

    return build_index(all_chunks, all_meta), skipped


def answer_question(question: str, collection: Collection, client: genai.Client,
                    today: date | None = None,
                    generate: Callable[[genai.Client, str, list[str]], str] = generate_answer) -> dict:
    """Answer one question from the chunks that pass the relevance filter, and write its audit row.

    Returns the dict /query sends. `today` fixes the date for review checks (the eval passes
    one); `generate` replaces the model call (the eval passes a cached one).
    Raises ModelUnavailable if generating the answer fails.
    """
    all_texts, all_ids, all_distances, all_metas = search(collection, question, TOP_K)

    chunk_texts = []
    chunk_ids = []
    distances = []
    metas = []
    for text, chunk_id, distance, meta in zip(all_texts, all_ids, all_distances, all_metas):
        if distance < RELEVANCE_CUTOFF:
            chunk_texts.append(text)
            chunk_ids.append(chunk_id)
            distances.append(distance)
            metas.append(meta)

    if len(chunk_texts) == 0:
        log_query_db(question, "BRONZE", "no relevant source in the corpus", [], [0.0], [])
        return {
            "question": question,
            "answer": "No sufficiently relevant source found.",
            "tier": "BRONZE",
            "reason": "no relevant source in the corpus",
            "warnings": [],
            "sources": [],
            "distances": []
        }

    warnings = []
    for m in metas:
        if is_expired(m["review_date"], today):
            warnings.append("expired source: " + m["source"])
        if m["approved"] == False:
            warnings.append("unapproved source: " + m["source"])

    # As before P0.1, any failure here is reported as "model unavailable"; P0.4 narrows it.
    try:
        answer = generate(client, question, chunk_texts)
    except Exception as e:
        raise ModelUnavailable("generating the answer failed") from e

    tier, reason = classify(metas, distances, answer, today)

    log_query_db(question, tier, reason, chunk_ids, distances, warnings)

    return {
        "question": question,
        "answer": answer,
        "tier": tier,
        "reason": reason,
        "warnings": warnings,
        "sources": chunk_ids,
        "distances": distances
    }
