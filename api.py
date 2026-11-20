import os
from datetime import date
from dotenv import load_dotenv
from google import genai
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ingest import load_document, load_pdf, clean_text, clean_pdf_text, chunk_by_article
from retrieve import add_to_index
from govern import is_expired
from db import read_log_db, read_documents, set_approved, set_retired, add_document
from pipeline import ModelUnavailable, answer_question, index_documents

load_dotenv()
client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

collection, skipped_documents = index_documents(read_documents())
chunk_counter = collection.count()

app = FastAPI()

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
def health():
    return {"status": "ok", "skipped_documents": skipped_documents}


@app.post("/query")
def query(q: Query):
    if not q.question.strip():
        raise HTTPException(status_code=400, detail="question cannot be empty")

    try:
        return answer_question(q.question, collection, client)
    except ModelUnavailable:
        raise HTTPException(status_code=503, detail="model unavailable, please retry")


@app.get("/documents")
def documents():
    docs = read_documents()
    for d in docs:
        d["expired"] = is_expired(d["review_date"])
    return {"count": len(docs), "documents": docs}


@app.post("/upload")
async def upload(
    file: UploadFile = File(...),
    owner: str = Form(...),
    version: str = Form(...),
    review_date: str = Form(...)
):
    global chunk_counter

    try:
        date.fromisoformat(review_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="review_date must be YYYY-MM-DD")

    filename = file.filename
    if not filename or filename.startswith(".") or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="filename must be a plain file name")

    added = add_document(filename, owner, version, review_date)
    if not added:
        raise HTTPException(status_code=409, detail="document already exists")

    path = "documents/" + filename
    contents = await file.read()
    with open(path, "wb") as f:
        f.write(contents)

    if filename.endswith(".pdf"):
        text = clean_pdf_text(load_pdf(path))
    else:
        text = load_document(path)

    cleaned = clean_text(text)
    doc_chunks = chunk_by_article(cleaned)

    metas = []
    for chunk in doc_chunks:
        metas.append({
            "source": filename,
            "owner": owner,
            "version": version,
            "approved": False,
            "review_date": review_date
        })

    add_to_index(collection, doc_chunks, metas, chunk_counter)
    chunk_counter = chunk_counter + len(doc_chunks)

    return {"filename": filename, "chunks": len(doc_chunks), "approved": False}


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