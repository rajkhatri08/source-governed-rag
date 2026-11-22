import logging
import os
import threading
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from google import genai
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.datastructures import State
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.exc import OperationalError

from govern import is_expired
from db import (DuplicateDocument, database_reachable, delete_document, document_exists, insert_document,
                read_documents, read_documents_to_index, read_log_db, set_approved, set_retired)
from pipeline import ModelUnavailable, answer_question, chunk_rows, index_document, index_documents
from retrieve import embed, new_index
from upload import MAX_UPLOAD_BYTES, UploadRejected, prepare_upload

load_dotenv()
logger = logging.getLogger(__name__)

STARTUP_RETRY_WAITS = [1, 2, 4, 8]  # seconds between attempts: Neon's free compute takes a moment to wake
INDEXING_FAILED = "indexing the document failed, so nothing was saved; please try again"
# One upload at a time: two large PDFs parsed and embedded at once could exhaust a 512 MB server.
UPLOAD_LOCK = threading.Lock()


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
    """Runs once, before the first request is served: load the embedding model, index the text stored in Postgres."""
    # The one embedding model every question, upload and re-embedding uses (retrieve.EMBEDDER), loaded now so
    # that the first question doesn't wait for it: on Render, whose disk is ephemeral, that includes downloading it.
    try:
        embed(["warm-up"])
    except Exception:
        logger.exception("loading the embedding model failed at startup; the first question will try again")
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
    """Check everything, embed, then one insert, then index. A failed upload leaves nothing behind.

    A plain `def`, not `async def`: FastAPI runs it in a worker thread, so parsing and embedding a
    large PDF don't hold up other requests. Nothing is written to disk. One upload at a time: another
    one arriving meanwhile is refused with 503.
    """
    if not UPLOAD_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=503, detail="another upload is being indexed; please try again in a minute",
                            headers={"Retry-After": "60"})
    try:
        data = file.file.read(MAX_UPLOAD_BYTES + 1)  # one byte over the limit is enough to know it's too big
        return process_upload(request.app.state, file.filename, data,
                              owner=owner, version=version, review_date=review_date)
    finally:
        UPLOAD_LOCK.release()


def already_exists(filename: str) -> HTTPException:
    return HTTPException(status_code=409, detail=f"a document named {filename} already exists")


def process_upload(state: State, raw_name: str | None, data: bytes, *, owner: str, version: str,
                   review_date: str) -> dict:
    """Prepare, embed, insert, index: the upload itself, run while UPLOAD_LOCK is held. `state` is app.state."""
    try:
        prepared = prepare_upload(raw_name, data, owner=owner, version=version, review_date=review_date)
    except UploadRejected as e:
        raise HTTPException(status_code=e.status, detail=e.message)

    # A quick check, so a duplicate isn't embedded for minutes first. Postgres's unique constraint still decides:
    # two uploads of one name can both pass this check, and the second one's insert then gets the 409.
    if document_exists(prepared.filename):
        raise already_exists(prepared.filename)

    # The slow, memory-hungry step comes before anything is stored: if the server dies here, nothing is left
    # behind for the next start to trip over.
    try:
        embeddings = embed(prepared.chunks)
    except Exception:
        logger.exception(f"embedding the upload {prepared.filename} failed; nothing was stored")
        raise HTTPException(status_code=500, detail=INDEXING_FAILED)

    try:
        document_id = insert_document(filename=prepared.filename, owner=prepared.owner, version=prepared.version,
                                      review_date=prepared.review_date, content=prepared.content,
                                      content_sha256=prepared.content_sha256,
                                      chunks=chunk_rows(prepared.filename, prepared.chunks, embeddings))
    except DuplicateDocument:
        raise already_exists(prepared.filename)

    doc = {"filename": prepared.filename, "owner": prepared.owner, "version": prepared.version,
           "approved": False, "review_date": prepared.review_date}
    try:
        index_document(state.collection, doc, prepared.chunks, embeddings)
    except Exception:
        # Whatever failed, the upload must not half-exist: remove the row (its chunks go with it), then report it.
        logger.exception(f"indexing the upload {prepared.filename} failed; removing its row")
        delete_document(document_id)
        raise HTTPException(status_code=500, detail=INDEXING_FAILED)
    state.indexed_documents.add(prepared.filename)

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
