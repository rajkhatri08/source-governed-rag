import re
from sentence_transformers import SentenceTransformer
from sentence_transformers import util
with open("sample.txt", "r") as f:
    text = f.read()
def chunk_text(document,size, overlap):
    if overlap >= size:
        raise ValueError("overlap must be smaller than size")
    chunks = []
    for churn in range(0,len(document),size-overlap):
        chunks.append(document[churn:churn+size])
    return chunks

def clean_text(document):
    document = re.sub(r"\[\d+\]", "", document)
    lines = document.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if stripped:
            cleaned.append(stripped)
    return "\n".join(cleaned)
cleaned_text = clean_text(text)
print(len(text))
print(len(cleaned_text))

small = chunk_text(cleaned_text, 300,50)
big = chunk_text(cleaned_text, 1000,50)

print(len(small))
print(len(big))
print(small[0][-50:])
print(small[1][:50])
print(cleaned_text[:500])
matches = []
for chunk in small:
    if "controller" in chunk.lower():
        matches.append(chunk)

print(len(matches))
print(matches[0])
question = "what is the deadline for reporting a security incident"
words = question.lower().split()
print(words)
score = 0
for word in words:
    if word in small[0].lower():
        score = score + 1
print(score)
scored = []
for chunk in small:
    score = 0
    for word in words:
        if word in chunk.lower():
            score = score + 1
    scored.append((score, chunk))

print(scored[0][0])
print(scored[5][0])
all_scores = []
for pair in scored:
    all_scores.append(pair[0])
print(all_scores)
best = 0
best_chunk = ""
for pair in scored:
    if pair[0] > best:
        best = pair[0]
        best_chunk = pair[1]

print(best)
print(best_chunk)


model = SentenceTransformer("all-MiniLM-L6-v2")

test = model.encode("hello world")
print(len(test))
print(test[:10])
chunk_vectors = model.encode(small)
print(len(chunk_vectors))
print(len(chunk_vectors[0]))


query_vector = model.encode(question)
similarities = util.cos_sim(query_vector, chunk_vectors)

print(similarities.shape)
print(similarities[0][:5])
best_index = similarities[0].argmax()
print(similarities[0][best_index])
print(small[best_index])