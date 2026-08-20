import os
from dotenv import load_dotenv
from google import genai

from ingest import load_document, clean_text, chunk_text
from retrieve import build_index, search
from generate import generate_answer

load_dotenv()
api_key = os.getenv("GOOGLE_API_KEY")
genai_client = genai.Client(api_key=api_key)

text = load_document("sample.txt")
cleaned = clean_text(text)
chunks = chunk_text(cleaned, 300, 50)

collection = build_index(chunks)
question = "what is the deadline for reporting a security incident"

chunk_texts, chunk_ids, distances = search(collection, question, 3)
answer = generate_answer(genai_client, question, chunk_texts)
print(answer)
print()
print("Sources:")
for i in range(len(chunk_ids)):
    print(chunk_ids[i], "distance:", round(distances[i], 3))