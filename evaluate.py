from ingest import load_document, clean_text, chunk_text
from retrieve import build_index, search
from eval_questions import QUESTIONS

text = load_document("sample.txt")
cleaned = clean_text(text)
chunks = chunk_text(cleaned, 300, 50)
collection = build_index(chunks)

for question, answerable, source in QUESTIONS:
    chunk_texts, chunk_ids, distances = search(collection, question, 3)
    print(round(distances[0], 3), answerable, source, "|", question[:50])