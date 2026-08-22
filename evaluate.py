import time
import os
from dotenv import load_dotenv
from google import genai
from generate import generate_answer

load_dotenv()
genai_client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
from ingest import load_document, clean_text, chunk_text
from retrieve import build_index, search
from eval_questions import QUESTIONS

text = load_document("sample.txt")
cleaned = clean_text(text)
chunks = chunk_text(cleaned, 1000, 50)
collection = build_index(chunks)

for question, answerable, source in QUESTIONS:
    chunk_texts, chunk_ids, distances = search(collection, question, 3)

    try:
        answer = generate_answer(genai_client, question, chunk_texts)
        refused = "don't know" in answer.lower()
    except Exception as e:
        refused = None
        print("FAILED:", question[:40], type(e).__name__)

    print(round(distances[0], 3), answerable, refused, "|", question[:50])
    time.sleep(13)