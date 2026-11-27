import logging
import os
import resource
import sys
import threading
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response, UploadFile, File, Form
from fastapi.datastructures import State
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import OperationalError

from govern import is_expired
from db import (DuplicateDocument, database_reachable, delete_document, document_exists, insert_document,
                read_documents, read_documents_to_index, read_log_db, set_approved, set_retired)
from generate import new_client
from pipeline import ModelUnavailable, answer_question, chunk_rows, index_document, index_documents
from retrieve import embed, new_index
from upload import MAX_UPLOAD_BYTES, UploadRejected, prepare_upload

load_dotenv()
# Logging is configured once, here, because api.py is what uvicorn loads. INFO, so that the timings and the startup
# and upload lines reach the log; each line says when it was written and how serious it is.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

STARTUP_RETRY_WAITS = [1, 2, 4, 8]  # seconds between attempts: Neon's free compute takes a moment to wake
INDEXING_FAILED = "indexing the document failed, so nothing was saved; please try again"
# One upload at a time: two large PDFs parsed and embedded at once could exhaust a 512 MB server.
UPLOAD_LOCK = threading.Lock()


def peak_memory_mb() -> float:
    """The most memory this process has held at once since it started, in MB. It never goes down.

    ru_maxrss counts kilobytes on Linux (Render) and bytes on macOS.
    """
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


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
    started = time.perf_counter()
    # The one embedding model every question, upload and re-embedding uses (retrieve.EMBEDDER), loaded now so
    # that the first question doesn't wait for it: on Render, whose disk is ephemeral, that includes downloading it.
    try:
        embed(["warm-up"])
        logger.info(f"startup: embedding model ready in {time.perf_counter() - started:.1f} s")
    except Exception:
        logger.exception("loading the embedding model failed at startup; the first question will try again")
    app.state.client = new_client(os.getenv("GOOGLE_API_KEY"))
    app.state.collection = new_index()
    indexed, failed = index_documents(app.state.collection, read_documents_with_retry())
    app.state.indexed_documents = set(indexed)
    app.state.failed_documents = failed
    logger.info(f"startup: index ready in {time.perf_counter() - started:.1f} s: {len(indexed)} documents, "
                f"{app.state.collection.count()} chunks, {len(failed)} failed; peak memory {peak_memory_mb():.0f} MB")
    yield


app = FastAPI(lifespan=lifespan)


# Registered before CORSMiddleware on purpose: Starlette puts the middleware added last on the outside, so CORS
# wraps this one, and a 500 made here leaves with CORS headers the dashboard needs to read it. An exception that
# nothing catches is answered by Starlette outside CORS instead: the browser hides that reply, and a bug looks
# like "backend unreachable".
@app.middleware("http")
async def internal_error(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Any exception a route didn't handle: the traceback goes to the log, the client gets a fixed message."""
    try:
        return await call_next(request)
    except Exception:
        # !r: a path is decoded, so a filename with %0A in it could otherwise start a fake line in the log
        logger.exception(f"unhandled error on {request.method} {request.url.path!r}")
        return JSONResponse(status_code=500, content={"detail": "internal error"})


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "https://compliance-dashboard-omega-amber.vercel.app",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ModelUnavailable)
async def model_unavailable(request: Request, exc: ModelUnavailable) -> JSONResponse:
    """The model is busy, failing or out of reach: 503, which tells the client that a retry may work.

    Exception handlers run inside CORS, so the dashboard can read this reply too.
    """
    logger.warning(f"model unavailable: {exc}")
    return JSONResponse(status_code=503, content={"detail": "model unavailable, please retry"})


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
    """ModelUnavailable becomes a 503 (model_unavailable); any other failure, a 500 (internal_error)."""
    if not q.question.strip():
        raise HTTPException(status_code=400, detail="question cannot be empty")
    return answer_question(q.question, request.app.state.collection, request.app.state.client)


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
        raise HTTPException(status_code=503,
                            detail="another upload is being indexed; please try again in a few minutes",
                            headers={"Retry-After": "60"})
    started = time.perf_counter()
    try:
        data = file.file.read(MAX_UPLOAD_BYTES + 1)  # one byte over the limit is enough to know it's too big
        # Peak memory is logged before and after: it only ever rises, so a rise between the two lines is this
        # upload's own. Render's free tier shows no memory graph, so these lines are the measurement.
        logger.info(f"upload received: {len(data):,} bytes; peak memory so far {peak_memory_mb():.0f} MB")
        result = process_upload(request.app.state, file.filename, data,
                                owner=owner, version=version, review_date=review_date)
    finally:
        UPLOAD_LOCK.release()
    logger.info(f"upload stored {result['filename']}: {result['chunks']} chunks in "
                f"{time.perf_counter() - started:.1f} s; peak memory {peak_memory_mb():.0f} MB")
    return result


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
