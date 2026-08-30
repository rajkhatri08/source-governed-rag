import os
from datetime import date
from dotenv import load_dotenv
from google import genai
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ingest import load_document, load_pdf, clean_text, clean_pdf_text, chunk_by_article
from retrieve import build_index, search, add_to_index
from generate import generate_answer
from govern import is_expired, classify
from db import log_query_db, read_log_db, read_documents, set_approved, set_retired, add_document

load_dotenv()
client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

docs = read_documents()

all_chunks = []
all_meta = []

for d in docs:
    if d["retired"]:
        continue
    if d["filename"].endswith(".pdf"):
        text = clean_pdf_text(load_pdf("documents/" + d["filename"]))
    else:
        text = load_document("documents/" + d["filename"])
    cleaned = clean_text(text)
    doc_chunks = chunk_by_article(cleaned)
    for chunk in doc_chunks:
        chunk_meta = {
            "source": d["filename"],
            "owner": d["owner"],
            "version": d["version"],
            "approved": d["approved"],
            "review_date": d["review_date"]
        }
        all_chunks.append(chunk)
        all_meta.append(chunk_meta)

collection = build_index(all_chunks, all_meta)
chunk_counter = len(all_chunks)

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
    return {"status": "ok"}


@app.post("/query")
def query(q: Query):
    if not q.question.strip():
        raise HTTPException(status_code=400, detail="question cannot be empty")

    all_texts, all_ids, all_distances, all_metas = search(collection, q.question, 5)

    chunk_texts = []
    chunk_ids = []
    distances = []
    metas = []
    for i in range(len(all_ids)):
        if all_distances[i] < 1.2:
            chunk_texts.append(all_texts[i])
            chunk_ids.append(all_ids[i])
            distances.append(all_distances[i])
            metas.append(all_metas[i])

    if len(chunk_texts) == 0:
        log_query_db(q.question, "BRONZE", "no relevant source in the corpus", [], [0.0], [])
        return {
            "question": q.question,
            "answer": "No sufficiently relevant source found.",
            "tier": "BRONZE",
            "reason": "no relevant source in the corpus",
            "warnings": [],
            "sources": [],
            "distances": []
        }

    warnings = []
    for m in metas:
        if is_expired(m["review_date"]):
            warnings.append("expired source: " + m["source"])
        if m["approved"] == False:
            warnings.append("unapproved source: " + m["source"])

    try:
        answer = generate_answer(client, q.question, chunk_texts)
    except Exception:
        raise HTTPException(status_code=503, detail="model unavailable, please retry")

    tier, reason = classify(metas, distances, answer)

    log_query_db(q.question, tier, reason, chunk_ids, distances, warnings)

    return {
        "question": q.question,
        "answer": answer,
        "tier": tier,
        "reason": reason,
        "warnings": warnings,
        "sources": chunk_ids,
        "distances": distances
    }


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