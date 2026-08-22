import time
import os
from dotenv import load_dotenv
from google import genai
from generate import generate_answer
from ingest import load_document, clean_text, chunk_text
from retrieve import build_index, search
from eval_questions import QUESTIONS

load_dotenv()
client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
text = load_document("sample.txt")
cleaned = clean_text(text)
chunks = chunk_text(cleaned, 1000, 50)

collection = build_index(chunks)
question = "do I have the right to know who my data was shared with"

chunk_texts, chunk_ids, distances = search(collection, question, 3)
answer = generate_answer(client, question, chunk_texts)
print(answer)
print()
print("Sources:")
for i in range(len(chunk_ids)):
    print(chunk_ids[i], "distance:", round(distances[i], 3))
    print(chunk_texts[i])
    print("---")