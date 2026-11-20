import logging
import os
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from google import genai
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.exc import OperationalError

from govern import is_expired
from db import (DuplicateDocument, database_reachable, delete_document, insert_document, read_documents,
                read_documents_to_index, read_log_db, set_approved, set_retired)
from pipeline import ModelUnavailable, answer_question, index_document, index_documents
from retrieve import new_index
from upload import MAX_UPLOAD_BYTES, UploadRejected, prepare_upload

load_dotenv()
logger = logging.getLogger(__name__)

STARTUP_RETRY_WAITS = [1, 2, 4, 8]  # seconds between attempts: Neon's free compute takes a moment to wake


def read_documents_with_retry() -> list[dict]:
    """The documents to index at startup, retrying while the database wakes up.

    If the last attempt fails too, its error stops startup: the service doesn't run without its
    source of truth (fail fast), and Render restarts it.
    """
    for attempt, wait in enumerate(STARTUP_RETRY_WAITS, start=1):
        try:
            return read_documents_to_index()
        except OperationalError:
            logger.warning(f"database unreachable at startup (attempt {attempt}); retrying in {wait} s")
            time.sleep(wait)
    return read_documents_to_index()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs once, before the first request is served: index the text stored in Postgres."""
    app.state.client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
    app.state.collection = new_index()
    indexed, failed = index_documents(app.state.collection, read_documents_with_retry())
    app.state.indexed_documents = set(indexed)
    app.state.failed_documents = failed
    yield


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "https://compliance-dashboard-omega-amber.vercel.app",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


class Query(BaseModel):
    question: str


@app.get("/health")
def health(request: Request):
    """Always HTTP 200, so a database blip doesn't get the service restarted; "degraded" says what's wrong."""
    database = database_reachable()
    failed = request.app.state.failed_documents
    return {
        "status": "ok" if database and not failed else "degraded",
        "database": database,
        "documents_indexed": len(request.app.state.indexed_documents),
        "chunks_indexed": request.app.state.collection.count(),
        "failed_documents": failed,
    }


@app.post("/query")
def query(q: Query, request: Request):
    if not q.question.strip():
        raise HTTPException(status_code=400, detail="question cannot be empty")

    try:
        return answer_question(q.question, request.app.state.collection, request.app.state.client)
    except ModelUnavailable:
        raise HTTPException(status_code=503, detail="model unavailable, please retry")


@app.get("/documents")
def documents():
    docs = read_documents()
    for d in docs:
        d["expired"] = is_expired(d["review_date"])
    return {"count": len(docs), "documents": docs}


@app.post("/upload")
def upload(
    request: Request,
    file: UploadFile = File(...),
    owner: str = Form(...),
    version: str = Form(...),
    review_date: str = Form(...)
):
    """Check everything first, then one insert, then index. A failed upload leaves nothing behind.

    A plain `def`, not `async def`: FastAPI runs it in a worker thread, so parsing and embedding a
    large PDF don't hold up other requests. Nothing is written to disk.
    """
    data = file.file.read(MAX_UPLOAD_BYTES + 1)  # one byte over the limit is enough to know it's too big
    try:
        prepared = prepare_upload(file.filename, data, owner=owner, version=version, review_date=review_date)
    except UploadRejected as e:
        raise HTTPException(status_code=e.status, detail=e.message)

    try:
        document_id = insert_document(filename=prepared.filename, owner=prepared.owner, version=prepared.version,
                                      review_date=prepared.review_date, content=prepared.content,
                                      content_sha256=prepared.content_sha256)
    except DuplicateDocument:
        raise HTTPException(status_code=409, detail=f"a document named {prepared.filename} already exists")

    doc = {"filename": prepared.filename, "owner": prepared.owner, "version": prepared.version,
           "approved": False, "review_date": prepared.review_date}
    try:
        index_document(request.app.state.collection, doc, prepared.chunks)
    except Exception:
        # Whatever failed, the upload must not half-exist: remove the row, then report the failure.
        logger.exception(f"indexing the upload {prepared.filename} failed; removing its row")
        delete_document(document_id)
        raise HTTPException(status_code=500,
                            detail="indexing the document failed, so nothing was saved; please try again")
    request.app.state.indexed_documents.add(prepared.filename)

    return {"filename": prepared.filename, "chunks": len(prepared.chunks), "approved": False}


@app.post("/documents/{filename}/approve")
def approve(filename: str):
    found = set_approved(filename, True)
    if not found:
        raise HTTPException(status_code=404, detail="document not found")
    return {"filename": filename, "approved": True}


@app.post("/documents/{filename}/unapprove")
def unapprove(filename: str):
    found = set_approved(filename, False)
    if not found:
        raise HTTPException(status_code=404, detail="document not found")
    return {"filename": filename, "approved": False}


@app.post("/documents/{filename}/retire")
def retire(filename: str):
    found = set_retired(filename, True)
    if not found:
        raise HTTPException(status_code=404, detail="document not found")
    return {"filename": filename, "retired": True}


@app.get("/audit")
def audit():
    log = read_log_db()
    return {"count": len(log), "entries": log}
