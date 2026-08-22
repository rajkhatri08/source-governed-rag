import time
import os
from dotenv import load_dotenv
from google import genai
from generate import generate_answer
from ingest import load_document, clean_text, chunk_by_article, load_metadata
from retrieve import build_index, search
from eval_questions import QUESTIONS

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
        all_chunks.append(chunk)
        all_meta.append(metadata[filename])

collection = build_index(all_chunks, all_meta)

question = "how long do we have to report a data breach"

chunk_texts, chunk_ids, distances, metas = search(collection, question, 3)
answer = generate_answer(client, question, chunk_texts)
print(answer)
print()
print("Sources:")
for i in range(len(chunk_ids)):
    print(chunk_ids[i], "distance:", round(distances[i], 3))
    print(chunk_texts[i])
    print("---")
    print(metas[i])