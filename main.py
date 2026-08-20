from ingest import load_document, clean_text, chunk_text

text = load_document("sample.txt")
cleaned = clean_text(text)
chunks = chunk_text(cleaned, 300, 50)

print(len(chunks))
from retrieve import build_index, search

collection = build_index(chunks)
results = search(collection, "what is the deadline for reporting a security incident", 3)

print(results["documents"][0][0][:300])