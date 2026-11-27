from datetime import date

import httpx
import pytest
from google import genai
from google.genai import errors
from sqlalchemy.exc import OperationalError

import pipeline
from generate import HTTP_OPTIONS
from govern import classify, is_approved, source_warnings
from retrieve import EMBEDDING_MODEL, content_hash, make_chunk_id, new_index

TODAY = date(2026, 11, 18)
GDPR = {"filename": "gdpr_ch3.txt", "owner": "Legal & Compliance", "version": "2016/679",
        "approved": True, "review_date": "2027-05-25"}
TEXTS = [
    "Art. 33 GDPR | Notification of a personal data breach | 1. … not later than 72 hours after …",
    "Art. 17 GDPR | Right to erasure | 3. Paragraphs 1 and 2 shall not apply …",
]
IDS, METAS = pipeline.chunk_ids_and_metadata(GDPR, TEXTS)
RETENTION = {"filename": "retention_policy_v1.txt", "owner": "Data Governance", "version": "1.0",
             "approved": True, "review_date": "2023-01-15"}  # past review
RETENTION_TEXTS = ["Section 1. Scope", "Section 2. Breach Notification", "Section 3. Access Requests"]
RETENTION_IDS, RETENTION_METAS = pipeline.chunk_ids_and_metadata(RETENTION, RETENTION_TEXTS)


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


def answers(reply: str):
    """A stand-in for generate_answer that always replies `reply`."""
    return lambda client, question, chunk_texts: reply


def test_with_no_chunk_under_the_cutoff_the_model_is_not_asked_and_no_distance_is_logged(logged):
    collection = FakeCollection([(TEXTS[1], IDS[1], 1.3, METAS[1])])
    result = pipeline.answer_question("Who is our DPO?", collection, None, today=TODAY, generate=no_model)
    assert result["tier"] == "BRONZE"
    assert result["sources"] == []
    assert result["refused"] is True
    assert result["timings_ms"]["generation"] is None  # not 0: the model wasn't asked
    assert logged == [{"answer": "No sufficiently relevant source found.", "tier": "BRONZE",
                       "reason": "no relevant source in the corpus", "chunks": [], "warnings": []}]


def test_the_audit_row_names_exactly_the_chunks_that_passed_the_filter(logged):
    collection = FakeCollection([(TEXTS[0], IDS[0], 0.785, METAS[0]), (TEXTS[1], IDS[1], 1.3, METAS[1])])
    result = pipeline.answer_question("How fast must a breach be reported?", collection, None, today=TODAY,
                                      generate=answers("Within 72 hours."))
    assert result["sources"] == [IDS[0]]
    assert result["refused"] is False
    assert logged[0]["answer"] == "Within 72 hours."
    assert logged[0]["chunks"] == [{"chunk_id": IDS[0], "source": "gdpr_ch3.txt",
                                    "content_hash": content_hash(TEXTS[0]), "distance": 0.785}]


def test_a_refusal_shows_a_fixed_answer_in_the_response_and_in_the_audit_row(logged):
    collection = FakeCollection([(TEXTS[0], IDS[0], 0.9, METAS[0])])
    result = pipeline.answer_question("What is the maximum fine?", collection, None, today=TODAY,
                                      generate=answers("NO_ANSWER_IN_CONTEXT. The context covers deadlines only."))
    assert result["refused"] is True
    assert result["answer"] == pipeline.REFUSAL_ANSWER
    assert (result["tier"], result["reason"]) == ("BRONZE", "no answer found in the available sources")
    assert logged[0]["answer"] == pipeline.REFUSAL_ANSWER


def test_a_source_with_several_passing_chunks_is_warned_about_once(logged):
    collection = FakeCollection([(text, chunk_id, 1.0, meta)
                                 for text, chunk_id, meta in zip(RETENTION_TEXTS, RETENTION_IDS, RETENTION_METAS)])
    result = pipeline.answer_question("What does the retention policy say?", collection, None, today=TODAY,
                                      generate=answers("Records are kept for 6 years."))
    assert result["warnings"] == ["expired source: retention_policy_v1.txt"]
    assert logged[0]["warnings"] == ["expired source: retention_policy_v1.txt"]


def test_timings_cover_each_step_in_whole_milliseconds(logged):
    collection = FakeCollection([(TEXTS[0], IDS[0], 0.785, METAS[0])])
    result = pipeline.answer_question("How fast must a breach be reported?", collection, None, today=TODAY,
                                      generate=answers("Within 72 hours."))
    timings = result["timings_ms"]
    assert list(timings) == ["retrieval", "generation", "audit_write", "total"]
    assert all(isinstance(ms, int) and ms >= 0 for ms in timings.values())


def test_a_bug_while_answering_is_not_reported_as_model_unavailable(logged):
    def broken(client, question, chunk_texts):
        raise TypeError("a bug in our own code")

    collection = FakeCollection([(TEXTS[0], IDS[0], 0.785, METAS[0])])
    with pytest.raises(TypeError):
        pipeline.answer_question("How fast must a breach be reported?", collection, None, today=TODAY,
                                 generate=broken)
    assert logged == []


def fake_gemini(respond) -> genai.Client:
    """A real Gemini client with production's options, whose requests go to `respond` instead of to Google."""
    options = HTTP_OPTIONS.model_copy(update={"client_args": {"transport": httpx.MockTransport(respond)}})
    return genai.Client(api_key="test-key", http_options=options)


def reply_with(text: str) -> httpx.Response:
    """What the Gemini API sends back for a reply holding `text`."""
    return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}}]})


def times_out(request: httpx.Request):
    raise httpx.ReadTimeout("timed out", request=request)


def cannot_connect(request: httpx.Request):
    raise httpx.ConnectError("connection refused", request=request)


def overloaded(request: httpx.Request) -> httpx.Response:
    return httpx.Response(503, json={"error": {"code": 503, "message": "overloaded", "status": "UNAVAILABLE"}})


def ask_breach_question(client: genai.Client) -> dict:
    """The breach question through answer_question with the real generate_answer and SDK, on `client`."""
    collection = FakeCollection([(TEXTS[0], IDS[0], 0.785, METAS[0])])
    return pipeline.answer_question("How fast must a breach be reported?", collection, client, today=TODAY)


def test_each_model_call_has_a_one_minute_timeout(logged):
    timeouts = []

    def respond(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.extensions["timeout"])
        return reply_with("Within 72 hours.")

    assert ask_breach_question(fake_gemini(respond))["answer"] == "Within 72 hours."
    assert timeouts == [{"connect": 60.0, "read": 60.0, "write": 60.0, "pool": 60.0}]


@pytest.mark.parametrize("respond", [times_out, cannot_connect, overloaded])
def test_a_timeout_a_lost_connection_or_an_overloaded_model_is_model_unavailable(logged, respond):
    with pytest.raises(pipeline.ModelUnavailable):
        ask_breach_question(fake_gemini(respond))
    assert logged == []


def test_a_rejected_request_is_a_fault_to_fix_not_an_outage(logged):
    def rejected(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"code": 400, "message": "API key not valid",
                                                   "status": "INVALID_ARGUMENT"}})

    with pytest.raises(errors.APIError) as raised:
        ask_breach_question(fake_gemini(rejected))
    assert raised.value.code == 400
    assert logged == []


def test_a_reply_without_text_is_a_refusal_never_an_empty_answer(logged):
    def blocked(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}})

    result = ask_breach_question(fake_gemini(blocked))
    assert result["refused"] is True
    assert result["answer"] == pipeline.REFUSAL_ANSWER
    assert result["tier"] == "BRONZE"


class RecordingIndex:
    """Stands in for the Chroma collection at startup: records what each document adds, and can fail one."""

    def __init__(self, fail_for: str | None = None):
        self.added = {}
        self.embeddings = {}
        self.fail_for = fail_for

    def add(self, ids: list[str], documents: list[str], metadatas: list[dict], embeddings=None) -> None:
        source = metadatas[0]["source"]
        if source == self.fail_for:
            raise RuntimeError("embedding failed")
        self.added[source] = ids
        self.embeddings[source] = embeddings


FRESH = [9.0]  # what fake_embed gives every chunk: a vector computed now, not read from the database


@pytest.fixture(autouse=True)
def stored_vectors(monkeypatch) -> dict:
    """Stands in for document_chunks and the embedding model in every test here: no database, no model.

    Keys are (document id, model); values are {chunk_id: (content_hash, vector)}. Empty: nothing stored.
    """
    vectors = {}
    monkeypatch.setattr(pipeline, "read_stored_vectors",
                        lambda document_id, model: vectors.get((document_id, model), {}))
    monkeypatch.setattr(pipeline, "embed", lambda texts: [FRESH for _ in texts])
    return vectors


def stored(filename: str, content: str | None, document_id: int = 1) -> dict:
    """A document as read_documents_to_index returns it."""
    return {**GDPR, "id": document_id, "filename": filename, "content": content}


def test_startup_with_no_documents_indexes_nothing_and_fails_nothing():
    assert pipeline.index_documents(RecordingIndex(), []) == ([], [])


def test_a_document_without_stored_text_is_reported_and_the_others_are_still_indexed():
    index = RecordingIndex()
    indexed, failed = pipeline.index_documents(index, [stored("lost.txt", None), stored("gdpr_ch3.txt", TEXTS[0])])
    assert indexed == ["gdpr_ch3.txt"]
    assert failed == [{"filename": "lost.txt", "reason": "no stored content"}]
    assert index.added == {"gdpr_ch3.txt": [make_chunk_id("gdpr_ch3.txt", 0, TEXTS[0])]}


def test_a_document_that_fails_to_index_is_reported_and_startup_goes_on():
    index = RecordingIndex(fail_for="bad.txt")
    indexed, failed = pipeline.index_documents(index, [stored("bad.txt", TEXTS[1]), stored("gdpr_ch3.txt", TEXTS[0])])
    assert indexed == ["gdpr_ch3.txt"]
    assert failed == [{"filename": "bad.txt", "reason": "indexing failed"}]


def test_a_document_whose_vectors_are_all_stored_is_indexed_with_them(stored_vectors):
    stored_vectors[(1, EMBEDDING_MODEL)] = {IDS[0]: (content_hash(TEXTS[0]), [0.5, 0.25])}
    index = RecordingIndex()
    assert pipeline.index_documents(index, [stored("gdpr_ch3.txt", TEXTS[0])]) == (["gdpr_ch3.txt"], [])
    assert index.embeddings["gdpr_ch3.txt"] == [[0.5, 0.25]]


def test_a_document_without_stored_vectors_is_embedded_again_and_says_so(caplog):
    index = RecordingIndex()
    assert pipeline.index_documents(index, [stored("gdpr_ch3.txt", TEXTS[0])]) == (["gdpr_ch3.txt"], [])
    assert index.embeddings["gdpr_ch3.txt"] == [FRESH]
    assert "re-embedding gdpr_ch3.txt" in caplog.text


@pytest.mark.parametrize("change", ["one chunk missing", "text changed", "another model"])
def test_vectors_that_dont_match_every_current_chunk_are_not_used(stored_vectors, change):
    """The document is embedded again, never indexed with a stale or partial set of vectors."""
    first, second = "Section 1. Scope", "Section 2. Retention"  # the two chunks of the document below
    ids = [make_chunk_id("policy.txt", 0, first), make_chunk_id("policy.txt", 1, second)]
    rows = {ids[0]: (content_hash(first), [0.5]), ids[1]: (content_hash(second), [0.25])}
    model = EMBEDDING_MODEL
    if change == "one chunk missing":
        del rows[ids[1]]
    elif change == "text changed":
        rows[ids[1]] = ("0" * 64, [0.25])  # the same id, but the full hash of some other text
    else:
        model = "an older model"
    stored_vectors[(1, model)] = rows
    index = RecordingIndex()
    pipeline.index_documents(index, [stored("policy.txt", f"{first}\n{second}")])
    assert index.added["policy.txt"] == ids
    assert index.embeddings["policy.txt"] == [FRESH, FRESH]  # the whole document embedded again


def test_if_the_stored_vectors_cant_be_read_the_document_is_embedded_and_still_indexed(monkeypatch):
    def unreadable(document_id, model):
        raise OperationalError("SELECT ...", {}, Exception("connection lost"))

    monkeypatch.setattr(pipeline, "read_stored_vectors", unreadable)
    index = RecordingIndex()
    assert pipeline.index_documents(index, [stored("gdpr_ch3.txt", TEXTS[0])]) == (["gdpr_ch3.txt"], [])
    assert index.embeddings["gdpr_ch3.txt"] == [FRESH]


def indexed_at_startup(**row_changes) -> dict:
    """GDPR's row, with these changes, indexed by startup into a real in-memory index.

    Returns its chunk's metadata as a search returns it, and fails if startup couldn't index the document.
    """
    collection = new_index()  # nothing is embedded or downloaded: the autouse fixture supplies the vector
    row = {**stored("gdpr_ch3.txt", TEXTS[0]), **row_changes}
    assert pipeline.index_documents(collection, [row]) == (["gdpr_ch3.txt"], [])
    return collection.query(query_embeddings=[FRESH], n_results=1)["metadatas"][0][0]


def test_an_approved_document_comes_back_from_a_real_index_as_true():
    """is_approved needs exactly True: if the index gave back 1 or "true" instead, every answer would turn BRONZE."""
    meta = indexed_at_startup(approved=True)
    assert meta["approved"] is True
    assert is_approved(meta)


@pytest.mark.parametrize("approved", [False, None])  # None: a NULL in Postgres, which the index would refuse
def test_an_unapproved_or_null_approved_document_is_indexed_and_makes_answers_bronze(approved):
    meta = indexed_at_startup(approved=approved)
    assert meta["approved"] is False
    assert classify([meta], [0.8], "Within 72 hours.", TODAY) == ("BRONZE", "source not approved: gdpr_ch3.txt")


def test_a_document_with_a_null_review_date_is_indexed_and_counts_as_past_review():
    meta = indexed_at_startup(review_date=None)
    assert classify([meta], [0.8], "Within 72 hours.", TODAY) == (
        "SILVER", "source has no valid review date, so it counts as past review: gdpr_ch3.txt")
    assert source_warnings([meta], TODAY) == ["no valid review date: gdpr_ch3.txt"]


def test_the_stored_rows_carry_the_same_ids_as_the_index():
    rows = pipeline.chunk_rows("gdpr_ch3.txt", TEXTS, [[0.5], [0.25]])
    assert [row["chunk_id"] for row in rows] == IDS
    assert rows[1] == {"chunk_id": IDS[1], "chunk_index": 1, "content_hash": content_hash(TEXTS[1]),
                       "chunk_text": TEXTS[1], "embedding": [0.25], "embedding_model": EMBEDDING_MODEL}
