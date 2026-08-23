import os
from dotenv import load_dotenv
from google import genai
from generate import generate_answer
from ingest import load_document, clean_text, chunk_by_article, load_metadata
from retrieve import build_index, search
from govern import is_expired, classify

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

question = "how quickly must AI-related data requests be answered"

chunk_texts, chunk_ids, distances, metas = search(collection, question, 5)

for m in metas:
    if is_expired(m["review_date"]):
        print("WARNING: expired source -", m["source"], "reviewed", m["review_date"])

for m in metas:
    if m["approved"] == False:
        print("WARNING: unapproved source -", m["source"], "version", m["version"])

tier, reason = classify(distances[0], metas, distances)
print("TIER:", tier)
print()

answer = generate_answer(client, question, chunk_texts)
print(answer)
print()
print("Sources:")
for i in range(len(chunk_ids)):
    print(chunk_ids[i], "distance:", round(distances[i], 3))
    print(chunk_texts[i])
    print("---")
    print(metas[i])