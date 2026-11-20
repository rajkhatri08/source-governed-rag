import os
import json
from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime, timezone

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost:5432/compliance")

engine = create_engine(DATABASE_URL)
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


def add_document(filename, owner, version, review_date):
    session = Session()
    existing = session.query(Document).filter(Document.filename == filename).first()
    if existing:
        session.close()
        return False
    doc = Document(
        filename=filename,
        owner=owner,
        version=version,
        approved=False,
        retired=False,
        review_date=review_date
    )
    session.add(doc)
    session.commit()
    session.close()
    return True