"""Store the vectors of every active document's chunks, so startup reads them instead of computing them.

Run from the repo root, after backfill_content.py:
    python backfill_vectors.py --dry-run    report what it would store; compute and change nothing
    python backfill_vectors.py              compute the missing vectors and store them

DATABASE_URL comes from the shell if it is set there, else .env (Ship 1: DATABASE_URL='<Neon URL>' ...).
The vectors are computed by retrieve.embed, exactly as the app computes them, so run it with the
chromadb version in requirements.txt. Idempotent: a chunk whose vector is already stored is left
alone, so a second run changes nothing. One transaction: if anything goes wrong, nothing is stored.
"""
import argparse
from datetime import datetime, timezone
from urllib.parse import urlparse

import db
from ingest import chunk_document
from pipeline import chunk_rows
from retrieve import EMBEDDING_MODEL, embed, make_chunk_id


def store_missing_vectors(session, *, dry_run: bool = False) -> int:
    """Add the document_chunks rows the active documents lack; returns how many. Prints one line per document."""
    added = 0
    documents = session.query(db.Document).filter(db.Document.retired.isnot(True)).order_by(db.Document.id).all()
    for document in documents:
        if not document.content:
            print(f"  skipped  {document.filename}: no content (run backfill_content.py first)")
            continue
        chunks = chunk_document(document.content)
        stored = {chunk_id for (chunk_id,) in session.query(db.DocumentChunk.chunk_id).filter(
            db.DocumentChunk.document_id == document.id, db.DocumentChunk.embedding_model == EMBEDDING_MODEL)}
        ids = [make_chunk_id(document.filename, index, chunk) for index, chunk in enumerate(chunks)]
        missing = [chunk_id for chunk_id in ids if chunk_id not in stored]
        if not missing:
            print(f"  complete {document.filename}: all {len(chunks)} chunks have vectors")
            continue
        if not dry_run:
            # The whole document is embedded in one call (a document's rows are always stored together,
            # so in practice either all of its chunks are missing or none are).
            rows = chunk_rows(document.filename, chunks, embed(chunks))
            now = datetime.now(timezone.utc)
            session.add_all([db.DocumentChunk(document_id=document.id, created_at=now, **row)
                             for row in rows if row["chunk_id"] not in stored])
        added += len(missing)
        print(f"  {'would add' if dry_run else 'added'} {document.filename}: "
              f"vectors for {len(missing)} of {len(chunks)} chunks")
    return added


def main() -> None:
    parser = argparse.ArgumentParser(description="Store the vectors of every active document's chunks.")
    parser.add_argument("--dry-run", action="store_true", help="report what would be stored, then roll back")
    args = parser.parse_args()

    parsed = urlparse(db.DATABASE_URL)
    print(f"database: {parsed.hostname}/{parsed.path.lstrip('/')}")

    session = db.Session()
    try:
        added = store_missing_vectors(session, dry_run=args.dry_run)
        if args.dry_run:
            session.rollback()
            print(f"dry run: {added} chunks would get vectors; nothing was changed")
        else:
            session.commit()
            print(f"committed: {added} chunks got vectors")
    finally:
        session.close()


if __name__ == "__main__":
    main()
