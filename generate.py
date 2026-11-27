import logging

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

MODEL = "gemini-3.6-flash"
# Sent with every call, and part of the eval's cache key, since any of it can change an answer. No tools are used,
# so automatic function calling is off: left on, the SDK logs about it on every call. The SDK keeps this setting to
# itself; it isn't sent to Google. Thinking stays at the model's default: at MINIMAL, calls took half as long, but
# the model refused questions whose sources disagree, such as "what is our deadline for reporting a personal data
# breach?" (refused 5 times out of 5; docs/eval/p0.4.md).
GENERATION_CONFIG = {"automatic_function_calling": {"disable": True}}
# The SDK sets no timeout of its own, so a hung call would hold its request for ever. This gives each phase of a
# call (connecting, sending, waiting for the reply) 60 seconds; the SDK counts in milliseconds. It doesn't retry
# unless told to, so a call that hangs ends within about a minute.
HTTP_OPTIONS = types.HttpOptions(timeout=60_000)

PROMPT_TEMPLATE = """Answer the question using only the context below.
If the context does not contain the answer, reply with exactly NO_ANSWER_IN_CONTEXT and nothing else.

Context:
{context}

Question: {question}

Answer:"""


def new_client(api_key: str | None) -> genai.Client:
    """A Gemini client whose calls time out (HTTP_OPTIONS). The API and the evaluation both use it."""
    return genai.Client(api_key=api_key, http_options=HTTP_OPTIONS)


def generate_answer(client: genai.Client, question: str, chunks: list[str]) -> str:
    """Ask the model to answer the question from the given chunks only. "" if the reply holds no text."""
    context = "\n\n".join(chunks)
    prompt = PROMPT_TEMPLATE.format(context=context, question=question)
    response = client.models.generate_content(model=MODEL, contents=prompt, config=GENERATION_CONFIG)
    if not response.text:
        # A blocked prompt or reply comes back without text. "" counts as a refusal (govern.is_refusal), so it
        # can't pass as an answer.
        block_reason = response.prompt_feedback.block_reason if response.prompt_feedback else None
        finish_reason = response.candidates[0].finish_reason if response.candidates else None
        logger.warning(f"the model returned no text (block reason: {block_reason}, finish reason: {finish_reason})")
        return ""
    return response.text
