"""The question-answering pipeline, shared by the API and the evaluation."""
import logging
import time
from collections.abc import Callable
from datetime import date

import httpx
from chromadb import Collection
from google import genai
from google.genai import errors
from sqlalchemy.exc import SQLAlchemyError

from db import log_query_db, read_stored_vectors
from generate import generate_answer
from govern import RELEVANCE_CUTOFF, classify, is_refusal, source_warnings
from ingest import chunk_document
from retrieve import EMBEDDING_MODEL, add_to_index, content_hash, embed, make_chunk_id, search

logger = logging.getLogger(__name__)

TOP_K = 5
NO_SOURCE_ANSWER = "No sufficiently relevant source found."
REFUSAL_ANSWER = "The sources found don't contain the answer to this question."
# HTTP codes meaning the model's service is busy or failing right now, not that our request is wrong: the ones
# google-genai itself retries on. Any other APIError (a bad request, a bad key) is a fault to fix, not an outage.
MODEL_UNAVAILABLE_CODES = (408, 429, 500, 502, 503, 504)

GenerateFunction = Callable[[genai.Client, str, list[str]], str]  # generate_answer's signature


class ModelUnavailable(Exception):
    """The model can't answer right now: its service is busy or failing, or it can't be reached. The API sends 503."""


def ms_since(start: float) -> int:
    """Whole milliseconds since `start`, a time.perf_counter() reading."""
    return round((time.perf_counter() - start) * 1000)


def call_model(generate: GenerateFunction, client: genai.Client, question: str, chunk_texts: list[str]) -> str:
    """The model's reply. Its outages become ModelUnavailable; anything else, a bug included, propagates as it is."""
    try:
        return generate(client, question, chunk_texts)
    except errors.APIError as e:
        if e.code in MODEL_UNAVAILABLE_CODES:
            raise ModelUnavailable(f"the model returned HTTP {e.code}") from e
        raise
    except httpx.TransportError as e:
        # A timeout or a connection that failed: no HTTP reply at all. The SDK lets httpx's own errors through.
        raise ModelUnavailable(f"the model couldn't be reached ({type(e).__name__})") from e


def chunk_ids_and_metadata(doc: dict, chunks: list[str]) -> tuple[list[str], list[dict]]:
    """The index ids and metadata for one document's chunks.

    The index refuses None as a metadata value, so a NULL approval or review date is stored as what the tier rules
    read as unknown: not approved, and no valid review date. Stored as None, the document would fail to index, and
    neither rule could ever apply to it.
    """
    ids = []
    metadatas = []
    for index, chunk in enumerate(chunks):
        ids.append(make_chunk_id(doc["filename"], index, chunk))
        metadatas.append({
            "source": doc["filename"],
            "owner": doc["owner"],
            "version": doc["version"],
            "approved": doc["approved"] is True,  # NULL → False: unapproved (fail closed)
            "review_date": doc["review_date"] or "",  # NULL → "": counts as no valid review date
            "chunk_index": index,
            "content_hash": content_hash(chunk),
        })
    return ids, metadatas


def chunk_rows(filename: str, chunks: list[str], embeddings: list[list[float]]) -> list[dict]:
    """The document_chunks rows for one document: each chunk's id, text, hash and vector."""
    return [{"chunk_id": make_chunk_id(filename, index, chunk), "chunk_index": index,
             "content_hash": content_hash(chunk), "chunk_text": chunk, "embedding": embedding,
             "embedding_model": EMBEDDING_MODEL}
            for index, (chunk, embedding) in enumerate(zip(chunks, embeddings))]


def index_document(collection: Collection, doc: dict, chunks: list[str], embeddings: list[list[float]]) -> None:
    """Add one document's chunks, with their vectors, to the index. Startup and /upload both use it."""
    ids, metadatas = chunk_ids_and_metadata(doc, chunks)
    add_to_index(collection, ids, chunks, metadatas, embeddings)


def stored_embeddings(doc: dict, chunks: list[str]) -> list[list[float]] | None:
    """The stored vectors of exactly these chunks, in order, or None unless every chunk has one.

    A vector counts only if the current model computed it from the same text (same id, same full
    hash). If the chunker or the model has changed since, or nothing was stored, it's None, and
    the caller embeds the document again: slower, never stale.
    """
    try:
        stored = read_stored_vectors(doc["id"], EMBEDDING_MODEL)
    except SQLAlchemyError:
        # The stored vectors are a cache: if they can't be read, embedding again costs time, not answers.
        logger.exception(f"reading the stored vectors of {doc['filename']} failed")
        return None
    embeddings = []
    for index, chunk in enumerate(chunks):
        hash_and_vector = stored.get(make_chunk_id(doc["filename"], index, chunk))
        if hash_and_vector is None or hash_and_vector[0] != content_hash(chunk):
            return None
        embeddings.append(hash_and_vector[1])
    return embeddings


def index_documents(collection: Collection, docs: list[dict]) -> tuple[list[str], list[dict]]:
    """Index each document's stored text, one document at a time, so one failure can't stop the rest.

    Uses each document's stored vectors when they match its chunks, and embeds it otherwise.
    Returns the filenames indexed and the failures as {filename, reason}. A reason is a short fixed
    phrase, safe for /health to show; the details go to the log.
    """
    indexed = []
    failed = []
    for d in docs:
        if not d["content"]:
            logger.warning(f"not indexing {d['filename']}: no stored content (run backfill_content.py)")
            failed.append({"filename": d["filename"], "reason": "no stored content"})
            continue
        try:
            chunks = chunk_document(d["content"])
            embeddings = stored_embeddings(d, chunks)
            if embeddings is None:
                logger.warning(f"re-embedding {d['filename']}: no stored vectors match its chunks "
                               "(run backfill_vectors.py)")
                embeddings = embed(chunks)
            index_document(collection, d, chunks, embeddings)
        except Exception:
            # The index and its embedding model can fail in many ways; one bad document must not
            # stop startup. The traceback goes to the log, and /health lists the document.
            logger.exception(f"indexing {d['filename']} failed at startup")
            failed.append({"filename": d["filename"], "reason": "indexing failed"})
            continue
        indexed.append(d["filename"])
    return indexed, failed


def answer_question(question: str, collection: Collection, client: genai.Client,
                    today: date | None = None, generate: GenerateFunction = generate_answer) -> dict:
    """Answer one question from the chunks that pass the relevance filter, and write its audit row.

    Returns the dict /query sends. `today` fixes the date for review checks (the eval passes
    one); `generate` replaces the model call (the eval passes a cached one).
    Raises ModelUnavailable if the model can't answer right now (see call_model).
    """
    started = time.perf_counter()
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
    # Milliseconds per step. generation stays None if the model isn't asked: 0 would claim it answered instantly.
    timings = {"retrieval": ms_since(started), "generation": None, "audit_write": None, "total": None}

    if chunk_texts:
        warnings = source_warnings(metas, today)
        generation_started = time.perf_counter()
        reply = call_model(generate, client, question, chunk_texts)  # what the model said
        timings["generation"] = ms_since(generation_started)
        refused = is_refusal(reply)
        # What the user sees: a fixed sentence instead of the sentinel and whatever the model wrote around it.
        answer = REFUSAL_ANSWER if refused else reply
        tier, reason = classify(metas, distances, reply, today)
    else:
        warnings = []
        refused = True
        answer = NO_SOURCE_ANSWER
        tier, reason = "BRONZE", "no relevant source in the corpus"

    # Each chunk the answer was drawn from, with enough to find its exact text again.
    audit_chunks = [{"chunk_id": chunk_id, "source": meta["source"], "content_hash": meta["content_hash"],
                     "distance": distance}
                    for chunk_id, meta, distance in zip(chunk_ids, metas, distances)]
    write_started = time.perf_counter()
    log_query_db(question, answer=answer, tier=tier, reason=reason, chunks=audit_chunks, warnings=warnings)
    timings["audit_write"] = ms_since(write_started)
    timings["total"] = ms_since(started)
    # Never the question itself: it may hold personal data.
    logger.info(f"answered: tier {tier}, refused {refused}, {len(chunk_ids)} chunks, timings_ms {timings}")

    return {
        "question": question,
        "answer": answer,
        "tier": tier,
        "reason": reason,
        "refused": refused,
        "warnings": warnings,
        "sources": chunk_ids,
        "distances": distances,
        "timings_ms": timings,
    }
