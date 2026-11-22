import os
import json
from dotenv import load_dotenv
from sqlalchemy import (create_engine, Column, ForeignKey, Integer, String, Float, DateTime, Text, Boolean, REAL,
                        UniqueConstraint, text)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime, timezone

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost:5432/compliance")
UNIQUE_VIOLATION = "23505"  # the SQLSTATE code Postgres gives a duplicate key, whatever the constraint is called

# pool_pre_ping tests each pooled connection before handing it out: Neon's free compute closes every
# connection when it sleeps, and without the check the first query after a pause would fail.
# connect_timeout bounds how long a connection attempt can hang, in seconds.
engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args={"connect_timeout": 10})
Session = sessionmaker(bind=engine)
Base = declarative_base()


class AuditEntry(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.now)  # the writing machine's clock, no zone: IST from the Mac, UTC on Render
    created_at = Column(DateTime(timezone=True))  # from P0.2: an exact instant, written in UTC
    question = Column(Text, nullable=False)
    answer = Column(Text)  # from P0.2
    tier = Column(String(10), nullable=False)
    reason = Column(Text)
    top_distance = Column(Float)  # the best passing chunk's distance; from P0.2, None when no chunk passed
    sources = Column(Text)  # JSON list of chunk ids
    chunks = Column(Text)  # from P0.2: JSON list of {chunk_id, source, content_hash, distance}
    warnings = Column(Text)


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True)
    filename = Column(String(200), unique=True, nullable=False)
    owner = Column(String(100))
    version = Column(String(50))
    approved = Column(Boolean, default=False)
    retired = Column(Boolean, default=False)
    review_date = Column(String(20))
    content = Column(Text)  # from P0.3: the extracted, cleaned text that is chunked and indexed
    content_sha256 = Column(String(64))  # from P0.3
    created_at = Column(DateTime(timezone=True))  # from P0.3: when the upload was stored, UTC; null for older rows


class DocumentChunk(Base):
    """From P0.3b: one chunk of a document with its vector, so startup can rebuild the index without the model.

    A cache, not a second source of truth: startup uses a vector only if it was computed by the current
    model from exactly the text that chunking documents.content gives today.
    """
    __tablename__ = "document_chunks"
    __table_args__ = (UniqueConstraint("chunk_id", "embedding_model",
                                       name="document_chunks_chunk_id_embedding_model_key"),)

    id = Column(Integer, primary_key=True)
    # ON DELETE CASCADE: deleting a document (only delete_document does) deletes its chunks with it
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    chunk_id = Column(String(255), nullable=False)  # retrieve.make_chunk_id: {filename}::{index}::{hash[:12]}
    chunk_index = Column(Integer, nullable=False)
    content_hash = Column(String(64), nullable=False)  # the SHA-256 of chunk_text
    chunk_text = Column(Text, nullable=False)
    embedding = Column(ARRAY(REAL), nullable=False)  # REAL is a 4-byte float: exactly what the model outputs
    embedding_model = Column(String(100), nullable=False)  # retrieve.EMBEDDING_MODEL
    created_at = Column(DateTime(timezone=True), nullable=False)


class DuplicateDocument(Exception):
    """Postgres refused the insert: a document with this filename already exists."""


def new_audit_entry(question: str, *, answer: str, tier: str, reason: str,
                    chunks: list[dict], warnings: list[str]) -> AuditEntry:
    """One audit row. `chunks` are the chunks that passed the relevance filter, best first.

    The `*` makes every argument after `question` keyword-only, so `answer` and `reason`
    (both strings) can't be swapped by position.
    """
    return AuditEntry(
        created_at=datetime.now(timezone.utc),
        question=question,
        answer=answer,
        tier=tier,
        reason=reason,
        top_distance=chunks[0]["distance"] if chunks else None,  # None, not 0.0: 0.0 would mean a perfect match
        sources=json.dumps([c["chunk_id"] for c in chunks]),
        chunks=json.dumps(chunks),
        warnings=json.dumps(warnings)
    )


def log_query_db(question: str, *, answer: str, tier: str, reason: str,
                 chunks: list[dict], warnings: list[str]) -> None:
    """Append one row to the audit log (see new_audit_entry)."""
    session = Session()
    session.add(new_audit_entry(question, answer=answer, tier=tier, reason=reason,
                                chunks=chunks, warnings=warnings))
    session.commit()
    session.close()


def audit_entry_to_dict(r: AuditEntry) -> dict:
    """An audit row as /audit returns it. Rows written before P0.2 have no answer, chunks or created_at: null."""
    if r.created_at is not None:
        timestamp = r.created_at.astimezone(timezone.utc).isoformat()  # Postgres returns it in the session's zone
    else:
        timestamp = r.timestamp.isoformat()  # before P0.2: the writing machine's clock, zone unknown
    return {
        "id": r.id,
        "timestamp": timestamp,
        "question": r.question,
        "answer": r.answer,
        "tier": r.tier,
        "reason": r.reason,
        "top_distance": r.top_distance,
        "sources": json.loads(r.sources),
        "chunks": json.loads(r.chunks) if r.chunks is not None else None,
        "warnings": json.loads(r.warnings)
    }


def read_log_db():
    session = Session()
    # Newest first by id, the order rows were written in: old timestamps mix IST and UTC, so they can't be sorted.
    rows = session.query(AuditEntry).order_by(AuditEntry.id.desc()).all()
    result = [audit_entry_to_dict(r) for r in rows]
    session.close()
    return result


def read_documents():
    session = Session()
    rows = session.query(Document).all()
    result = []
    for r in rows:
        result.append({
            "filename": r.filename,
            "owner": r.owner,
            "version": r.version,
            "approved": r.approved,
            "retired": r.retired,
            "review_date": r.review_date
        })
    session.close()
    return result


def set_approved(filename, approved):
    session = Session()
    doc = session.query(Document).filter(Document.filename == filename).first()
    if doc:
        doc.approved = approved
        session.commit()
    session.close()
    return doc is not None


def set_retired(filename, retired):
    session = Session()
    doc = session.query(Document).filter(Document.filename == filename).first()
    if doc:
        doc.retired = retired
        session.commit()
    session.close()
    return doc is not None


def is_unique_violation(error: IntegrityError, table: str) -> bool:
    """True if Postgres refused a duplicate key (SQLSTATE 23505) in `table`.

    Decided by the error code and table Postgres reports, not by a constraint's name, which
    can differ between databases.
    """
    original = error.orig
    return (getattr(original, "pgcode", None) == UNIQUE_VIOLATION
            and getattr(getattr(original, "diag", None), "table_name", None) == table)


def document_exists(filename: str) -> bool:
    """True if a document has this filename, retired or not.

    A quick check before an upload is embedded, so a duplicate is refused in seconds, not after minutes
    of embedding. It doesn't decide: insert_document's unique constraint does.
    """
    session = Session()
    try:
        return session.query(Document.id).filter(Document.filename == filename).first() is not None
    finally:
        session.close()


def insert_document(*, filename: str, owner: str, version: str, review_date: str,
                    content: str, content_sha256: str, chunks: list[dict]) -> int:
    """Store a new, unapproved document, its text and its chunks' vectors in one transaction; returns its id.

    `chunks` are document_chunks rows without their document_id (pipeline.chunk_rows builds them).
    There is no check-then-insert: Postgres's unique constraint decides, so two simultaneous
    uploads of one name can't both succeed. A duplicate raises DuplicateDocument.
    """
    session = Session()
    try:
        now = datetime.now(timezone.utc)
        doc = Document(filename=filename, owner=owner, version=version, approved=False, retired=False,
                       review_date=review_date, content=content, content_sha256=content_sha256, created_at=now)
        session.add(doc)
        session.flush()  # sends the INSERT, so a duplicate fails here and the new id is known
        session.add_all([DocumentChunk(document_id=doc.id, created_at=now, **chunk) for chunk in chunks])
        document_id = doc.id
        session.commit()
        return document_id
    except IntegrityError as e:
        session.rollback()
        if is_unique_violation(e, "documents"):
            raise DuplicateDocument(filename) from e
        raise
    finally:
        session.close()


def delete_document(document_id: int) -> None:
    """Undo insert_document when indexing the upload failed: a failed upload leaves no row behind.

    Its document_chunks rows go with it: Postgres deletes them (ON DELETE CASCADE).
    """
    session = Session()
    try:
        session.query(Document).filter(Document.id == document_id).delete()
        session.commit()
    finally:
        session.close()


def read_documents_to_index() -> list[dict]:
    """Every document that isn't retired, with its stored text, in the order they were added."""
    session = Session()
    try:
        rows = session.query(Document).filter(Document.retired.isnot(True)).order_by(Document.id).all()
        return [{"id": r.id, "filename": r.filename, "owner": r.owner, "version": r.version,
                 "approved": r.approved, "review_date": r.review_date, "content": r.content} for r in rows]
    finally:
        session.close()


def read_stored_vectors(document_id: int, embedding_model: str) -> dict[str, tuple[str, list[float]]]:
    """One document's stored vectors from one model, by chunk id, each with the hash of the text it came from.

    Read one document at a time, so startup holds only one document's vectors as Python lists at once.
    """
    session = Session()
    try:
        rows = (session.query(DocumentChunk.chunk_id, DocumentChunk.content_hash, DocumentChunk.embedding)
                .filter(DocumentChunk.document_id == document_id, DocumentChunk.embedding_model == embedding_model)
                .all())
        return {chunk_id: (content_hash, embedding) for chunk_id, content_hash, embedding in rows}
    finally:
        session.close()


def database_reachable() -> bool:
    """True if a trivial query succeeds right now. /health reports it."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False