import time
import os
import json
from dotenv import load_dotenv
from google import genai
from generate import generate_answer
from ingest import load_document, clean_text, chunk_by_article, load_metadata
from retrieve import build_index, search
from eval_questions import QUESTIONS

load_dotenv()
genai_client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

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
print("indexed chunks:", len(all_chunks))
print("collection count:", collection.count())

try:
    with open("cache.json", "r") as f:
        cache = json.load(f)
except:
    cache = {}

for question, answerable, source in QUESTIONS:
    chunk_texts, chunk_ids, distances, metas = search(collection, question, 3)

    key = question + "|" + str(len(all_chunks))
    if key in cache:
        answer = cache[key]
        refused = "don't know" in answer.lower()
    else:
        try:
            answer = generate_answer(genai_client, question, chunk_texts)
            refused = "don't know" in answer.lower()
            cache[key] = answer
            time.sleep(13)
        except Exception as e:
            refused = None
            print("FAILED:", question[:40], type(e).__name__)

    print(round(distances[0], 3), answerable, refused, "|", question[:50])

with open("cache.json", "w") as f:
    json.dump(cache, f)