"""The question-answering pipeline, shared by the API and the evaluation."""
import logging
from collections.abc import Callable
from datetime import date

from chromadb import Collection
from google import genai
from sqlalchemy.exc import SQLAlchemyError

from db import log_query_db, read_stored_vectors
from generate import generate_answer
from govern import RELEVANCE_CUTOFF, classify, is_expired
from ingest import chunk_document
from retrieve import EMBEDDING_MODEL, add_to_index, content_hash, embed, make_chunk_id, search

logger = logging.getLogger(__name__)

TOP_K = 5


class ModelUnavailable(Exception):
    """Generating the answer failed; the API reports it as 503."""


def chunk_ids_and_metadata(doc: dict, chunks: list[str]) -> tuple[list[str], list[dict]]:
    """The index ids and metadata for one document's chunks."""
    ids = []
    metadatas = []
    for index, chunk in enumerate(chunks):
        ids.append(make_chunk_id(doc["filename"], index, chunk))
        metadatas.append({
            "source": doc["filename"],
            "owner": doc["owner"],
            "version": doc["version"],
            "approved": doc["approved"],
            "review_date": doc["review_date"],
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
        answer = "No sufficiently relevant source found."
        reason = "no relevant source in the corpus"
        log_query_db(question, answer=answer, tier="BRONZE", reason=reason, chunks=[], warnings=[])
        return {
            "question": question,
            "answer": answer,
            "tier": "BRONZE",
            "reason": reason,
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

    # Each chunk the answer was drawn from, with enough to find its exact text again.
    audit_chunks = [{"chunk_id": chunk_id, "source": meta["source"], "content_hash": meta["content_hash"],
                     "distance": distance}
                    for chunk_id, meta, distance in zip(chunk_ids, metas, distances)]
    log_query_db(question, answer=answer, tier=tier, reason=reason, chunks=audit_chunks, warnings=warnings)

    return {
        "question": question,
        "answer": answer,
        "tier": tier,
        "reason": reason,
        "warnings": warnings,
        "sources": chunk_ids,
        "distances": distances
    }
