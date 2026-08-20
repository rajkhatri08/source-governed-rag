from google import genai


def generate_answer(client, question, chunks):
    context = "\n\n".join(chunks)

    prompt = f"""Answer the question using only the context below.
If the context does not contain the answer, say you don't know.

Context:
{context}

Question: {question}

Answer:"""

    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=prompt
    )
    return response.text