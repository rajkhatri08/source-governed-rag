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
text = load_document("documents/gdpr_ch3.txt")
metadata = load_metadata("documents/metadata.json")
print(metadata["gdpr_ch3.txt"]["approved"])
cleaned = clean_text(text)
chunks = chunk_by_article(cleaned)
collection = build_index(chunks, metadata["gdpr_ch3.txt"])
question = "can my data be kept if it is needed for legal claims"

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