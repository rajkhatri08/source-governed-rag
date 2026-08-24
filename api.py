import os
from dotenv import load_dotenv
from google import genai
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ingest import load_document, clean_text, chunk_by_article, load_metadata
from retrieve import build_index, search
from generate import generate_answer
from govern import is_expired, classify
from db import log_query_db, read_log_db, read_documents, set_approved

load_dotenv()
client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

metadata = load_metadata("documents/metadata.json")

all_chunks = []
all_meta = []

for filename in metadata:
    text = load_document("documents/" + filename)
    cleaned = clean_text(text)
    doc_chunks = chunk_by_article(cleaned)
    for chunk in doc_chunks:
        chunk_meta = dict(metadata[filename])
        chunk_meta["source"] = filename
        all_chunks.append(chunk)
        all_meta.append(chunk_meta)

collection = build_index(all_chunks, all_meta)

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


@app.get("/audit")
def audit():
    log = read_log_db()
    return {"count": len(log), "entries": log}