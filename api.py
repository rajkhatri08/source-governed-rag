import os
from dotenv import load_dotenv
from google import genai
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from ingest import load_document, clean_text, chunk_by_article, load_metadata
from retrieve import build_index, search
from generate import generate_answer
from govern import is_expired, classify
from db import log_query_db, read_log_db
from fastapi.middleware.cors import CORSMiddleware

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
    allow_origins=["http://localhost:3000"],
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

    chunk_texts, chunk_ids, distances, metas = search(collection, q.question, 5)

    warnings = []
    for i in range(len(metas)):
        if distances[i] < 1.2:
            m = metas[i]
            if is_expired(m["review_date"]):
                warnings.append("expired source: " + m["source"])
            if m["approved"] == False:
                warnings.append("unapproved source: " + m["source"])

    tier = classify(distances[0], metas, distances)

    try:
        answer = generate_answer(client, q.question, chunk_texts)
    except Exception:
        raise HTTPException(status_code=503, detail="model unavailable, please retry")

    log_query_db(q.question, tier, chunk_ids, distances, warnings)

    return {
        "question": q.question,
        "answer": answer,
        "tier": tier,
        "warnings": warnings,
        "sources": chunk_ids,
        "distances": distances
    }


@app.get("/documents")
def documents():
    docs = []
    for filename in metadata:
        m = metadata[filename]
        docs.append({
            "filename": filename,
            "owner": m["owner"],
            "version": m["version"],
            "approved": m["approved"],
            "review_date": m["review_date"],
            "expired": is_expired(m["review_date"])
        })
    return {"count": len(docs), "documents": docs}


@app.get("/audit")
def audit():
    log = read_log_db()
    return {"count": len(log), "entries": log}