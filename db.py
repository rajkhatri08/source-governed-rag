import os
import json
from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost:5432/compliance")

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
Base = declarative_base()


class AuditEntry(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.now)
    question = Column(Text, nullable=False)
    tier = Column(String(10), nullable=False)
    reason = Column(Text)
    top_distance = Column(Float)
    sources = Column(Text)
    warnings = Column(Text)


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True)
    filename = Column(String(200), unique=True, nullable=False)
    owner = Column(String(100))
    version = Column(String(50))
    approved = Column(Boolean, default=False)
    review_date = Column(String(20))


def init_db():
    Base.metadata.create_all(engine)


def log_query_db(question, tier, reason, sources, distances, warnings):
    session = Session()
    entry = AuditEntry(
        question=question,
        tier=tier,
        reason=reason,
        top_distance=distances[0],
        sources=json.dumps(sources),
        warnings=json.dumps(warnings)
    )
    session.add(entry)
    session.commit()
    session.close()


def read_log_db():
    session = Session()
    rows = session.query(AuditEntry).order_by(AuditEntry.timestamp.desc()).all()
    result = []
    for r in rows:
        result.append({
            "id": r.id,
            "timestamp": r.timestamp.isoformat(),
            "question": r.question,
            "tier": r.tier,
            "reason": r.reason,
            "top_distance": r.top_distance,
            "sources": json.loads(r.sources),
            "warnings": json.loads(r.warnings)
        })
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