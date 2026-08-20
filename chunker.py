import re
import chromadb
import os
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer
from sentence_transformers import util
print("DOTENV FOUND:", load_dotenv())
load_dotenv()
api_key = os.getenv("GOOGLE_API_KEY")
with open("sample.txt", "r") as f:
    text = f.read()

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


client = chromadb.Client()
collection = client.create_collection("gdpr")

ids = []
for i in range(len(small)):
    ids.append("chunk_" + str(i))

collection.add(documents=small, ids=ids)

print(collection.count())
results = collection.query(query_texts=[question], n_results=3)

print(results["ids"])
print(results["distances"])
print(results["documents"][0][0][:300])
from google import genai

genai_client = genai.Client(api_key=api_key)

response = genai_client.models.generate_content(
    model="gemini-3.6-flash",
    contents="Say hello in exactly three words."
)

print(response.text)