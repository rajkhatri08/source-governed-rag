from eval_questions import QUESTIONS
from ingest import load_document, clean_text, chunk_by_article, load_metadata
from retrieve import build_index, search

metadata = load_metadata("documents/metadata.json")

all_chunks = []
all_meta = []
for filename in metadata:
    text = load_document("documents/" + filename)
    cleaned = clean_text(text)
    for chunk in chunk_by_article(cleaned):
        cm = dict(metadata[filename])
        cm["source"] = filename
        all_chunks.append(chunk)
        all_meta.append(cm)

collection = build_index(all_chunks, all_meta)

results = []
for question, answerable, source in QUESTIONS:
    _, _, distances, _ = search(collection, question, 5)
    results.append((distances[0], answerable))

best_threshold = 0
best_correct = 0

t = 0.80
while t < 1.50:
    correct = 0
    for distance, answerable in results:
        predicted = distance < t
        if predicted == answerable:
            correct = correct + 1
    if correct > best_correct:
        best_correct = correct
        best_threshold = t
    t = t + 0.01

print("best threshold:", round(best_threshold, 2))
print("correct:", best_correct, "of", len(results))
print("accuracy:", round(best_correct / len(results) * 100, 1), "%")