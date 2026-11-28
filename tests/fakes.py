"""Stand-ins for the vector index and the Gemini API, shared by the pipeline and API tests."""
import httpx
from google import genai

from generate import HTTP_OPTIONS


class FakeCollection:
    """Stands in for the Chroma collection: query() returns the given (text, id, distance, metadata) results."""

    def __init__(self, results: list[tuple]):
        self.results = results

    def query(self, query_texts: list[str], n_results: int) -> dict:
        return {"documents": [[r[0] for r in self.results]], "ids": [[r[1] for r in self.results]],
                "distances": [[r[2] for r in self.results]], "metadatas": [[r[3] for r in self.results]]}


def fake_gemini(respond) -> genai.Client:
    """A real Gemini client with production's options, whose requests go to `respond` instead of to Google."""
    options = HTTP_OPTIONS.model_copy(update={"client_args": {"transport": httpx.MockTransport(respond)}})
    return genai.Client(api_key="test-key", http_options=options)


def reply_with(text: str) -> httpx.Response:
    """What the Gemini API sends back for a reply holding `text`."""
    return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}}]})
