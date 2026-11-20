from datetime import date

import pytest

import pipeline
from retrieve import content_hash

TODAY = date(2026, 11, 18)
GDPR = {"filename": "gdpr_ch3.txt", "owner": "Legal & Compliance", "version": "2016/679",
        "approved": True, "review_date": "2027-05-25"}
TEXTS = [
    "Art. 33 GDPR | Notification of a personal data breach | 1. … not later than 72 hours after …",
    "Art. 17 GDPR | Right to erasure | 3. Paragraphs 1 and 2 shall not apply …",
]
IDS, METAS = pipeline.chunk_ids_and_metadata(GDPR, TEXTS)


class FakeCollection:
    """Stands in for the Chroma collection: query() returns the given (text, id, distance, metadata) results."""

    def __init__(self, results: list[tuple]):
        self.results = results

    def query(self, query_texts: list[str], n_results: int) -> dict:
        return {"documents": [[r[0] for r in self.results]], "ids": [[r[1] for r in self.results]],
                "distances": [[r[2] for r in self.results]], "metadatas": [[r[3] for r in self.results]]}


@pytest.fixture
def logged(monkeypatch) -> list[dict]:
    """Replaces the audit write for one test: each row is recorded in this list instead of the database."""
    rows = []
    monkeypatch.setattr(pipeline, "log_query_db", lambda question, **row: rows.append(row))
    return rows


def no_model(client, question: str, chunk_texts: list[str]) -> str:
    raise AssertionError("the model must not be called")


def test_with_no_chunk_under_the_cutoff_the_model_is_not_asked_and_no_distance_is_logged(logged):
    collection = FakeCollection([(TEXTS[1], IDS[1], 1.3, METAS[1])])
    result = pipeline.answer_question("Who is our DPO?", collection, None, today=TODAY, generate=no_model)
    assert result["tier"] == "BRONZE"
    assert result["sources"] == []
    assert logged == [{"answer": "No sufficiently relevant source found.", "tier": "BRONZE",
                       "reason": "no relevant source in the corpus", "chunks": [], "warnings": []}]


def test_the_audit_row_names_exactly_the_chunks_that_passed_the_filter(logged):
    collection = FakeCollection([(TEXTS[0], IDS[0], 0.785, METAS[0]), (TEXTS[1], IDS[1], 1.3, METAS[1])])
    result = pipeline.answer_question("How fast must a breach be reported?", collection, None, today=TODAY,
                                      generate=lambda client, question, chunk_texts: "Within 72 hours.")
    assert result["sources"] == [IDS[0]]
    assert logged[0]["answer"] == "Within 72 hours."
    assert logged[0]["chunks"] == [{"chunk_id": IDS[0], "source": "gdpr_ch3.txt",
                                    "content_hash": content_hash(TEXTS[0]), "distance": 0.785}]
