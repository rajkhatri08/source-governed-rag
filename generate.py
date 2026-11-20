from google import genai

MODEL = "gemini-3.6-flash"

PROMPT_TEMPLATE = """Answer the question using only the context below.
If the context does not contain the answer, say you don't know.

Context:
{context}

Question: {question}

Answer:"""


def generate_answer(client: genai.Client, question: str, chunks: list[str]) -> str:
    """Ask the model to answer the question from the given chunks only."""
    context = "\n\n".join(chunks)
    prompt = PROMPT_TEMPLATE.format(context=context, question=question)
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt
    )
    return response.text
