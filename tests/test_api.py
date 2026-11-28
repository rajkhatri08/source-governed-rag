import logging
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from starlette.datastructures import State

import api
from db import DuplicateDocument, read_documents_to_index
from fakes import FakeCollection, fake_gemini, reply_with
from pdf_samples import blank_pdf, encrypted_pdf, numbered_sections
from pipeline import ModelUnavailable, chunk_ids_and_metadata
from retrieve import EMBEDDING_MODEL, content_hash, make_chunk_id, new_index
from upload import MAX_CHUNKS, MAX_PAGES, MAX_UPLOAD_BYTES

FIELDS = {"owner": "Operations", "version": "1.0", "review_date": "2027-01-15"}
VECTOR = [0.5, 0.25]  # what fake_embed gives every text
DASHBOARD = "https://compliance-dashboard-omega-amber.vercel.app"  # an origin CORSMiddleware allows


class FakeIndex:
    """Stands in for the Chroma collection that startup would have built."""

    def count(self) -> int:
        return 7


def fake_embed(texts: list[str]) -> list[list[float]]:
    """Stands in for the embedding model, which these tests never load."""
    return [VECTOR for _ in texts]


@pytest.fixture
def app_state(monkeypatch) -> State:
    """What startup would have put on app.state, made by hand: these tests never run the real startup.

    A fresh State for each test, swapped in whole: a test can change it freely, and the app gets
    its own back afterwards.
    """
    state = State({"collection": FakeIndex(), "client": None, "indexed_documents": {"gdpr_ch3.txt"},
                   "failed_documents": []})
    monkeypatch.setattr(api.app, "state", state)
    monkeypatch.setattr(api, "database_reachable", lambda: True)
    monkeypatch.setattr(api, "document_exists", lambda filename: False)
    monkeypatch.setattr(api, "embed", fake_embed)
    return state


@pytest.fixture
def client(app_state) -> TestClient:
    return TestClient(api.app)  # not `with TestClient(...)`, which would run the startup


def upload(client: TestClient, name: str, data: bytes, **fields):
    return client.post("/upload", files={"file": (name, data)}, data={**FIELDS, **fields})


def must_not_be_called(*args, **kwargs):
    raise AssertionError("an upload got further than it should have")


@pytest.mark.parametrize("name, data, fields, status", [
    ("../x.txt", b"text", {}, 400),
    (".env", b"KEY=value", {}, 400),
    ("a.exe", b"MZ", {}, 400),
    ("policy.txt", b"text", {"review_date": "next year"}, 400),
    ("big.txt", b"a" * (MAX_UPLOAD_BYTES + 1), {}, 413),
    ("long.pdf", blank_pdf(MAX_PAGES + 1), {}, 413),
    ("long.txt", numbered_sections(MAX_CHUNKS + 1), {}, 413),
    ("empty.txt", b"", {}, 422),
    ("scan.pdf", blank_pdf(), {}, 422),
    ("locked.pdf", encrypted_pdf("secret"), {}, 422),
    ("fake.pdf", b"%PDF-1.4 this is not really a PDF", {}, 422),
    ("notes.txt", "café".encode("cp1252"), {}, 422),
])
def test_a_rejected_upload_never_reaches_the_model_the_database_or_the_index(client, monkeypatch, name, data,
                                                                             fields, status):
    monkeypatch.setattr(api, "document_exists", must_not_be_called)
    monkeypatch.setattr(api, "embed", must_not_be_called)
    monkeypatch.setattr(api, "insert_document", must_not_be_called)
    monkeypatch.setattr(api, "index_document", must_not_be_called)
    response = upload(client, name, data, **fields)
    assert response.status_code == status
    assert isinstance(response.json()["detail"], str)
    assert not api.UPLOAD_LOCK.locked()  # a refusal still lets the next upload in


def test_an_accepted_upload_is_stored_with_its_text_and_vectors_then_indexed_unapproved(client, monkeypatch,
                                                                                         app_state):
    stored, indexed = [], []

    def insert(**row):
        stored.append(row)
        return 41

    monkeypatch.setattr(api, "insert_document", insert)
    monkeypatch.setattr(api, "index_document",
                        lambda collection, doc, chunks, embeddings: indexed.append((doc, chunks, embeddings)))
    text = "Section 1. Scope\nKeep records for 6 years."
    response = upload(client, "Retention Policy v2.txt", text.encode())
    assert response.status_code == 200
    assert response.json() == {"filename": "Retention Policy v2.txt", "chunks": 1, "approved": False}
    assert stored[0]["content"] == text
    assert stored[0]["chunks"] == [{"chunk_id": make_chunk_id("Retention Policy v2.txt", 0, text), "chunk_index": 0,
                                    "content_hash": content_hash(text), "chunk_text": text, "embedding": VECTOR,
                                    "embedding_model": EMBEDDING_MODEL}]
    doc, chunks, embeddings = indexed[0]
    assert doc["approved"] is False
    assert embeddings == [VECTOR]  # indexed with the vectors just stored, not embedded a second time
    assert "Retention Policy v2.txt" in app_state.indexed_documents


def test_an_upload_logs_its_size_then_its_chunks_seconds_and_peak_memory(client, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="api")
    monkeypatch.setattr(api, "insert_document", lambda **row: 41)
    monkeypatch.setattr(api, "index_document", lambda collection, doc, chunks, embeddings: None)
    assert upload(client, "policy.txt", b"Section 1. Scope").status_code == 200
    received, stored = [record.getMessage() for record in caplog.records if record.name == "api"]
    assert received.startswith("upload received: 16 bytes; peak memory so far ")
    assert stored.startswith("upload stored policy.txt: 1 chunks in ")
    assert stored.endswith(" MB")


def test_peak_memory_is_reported_in_megabytes():
    """Between 1 MB and 100 GB: kilobytes read as bytes, or bytes as kilobytes, would land far outside."""
    assert 1 < api.peak_memory_mb() < 100_000


def test_if_embedding_fails_nothing_is_stored_and_the_upload_reports_500(client, monkeypatch):
    def broken(texts):
        raise RuntimeError("model file missing")

    monkeypatch.setattr(api, "embed", broken)
    monkeypatch.setattr(api, "insert_document", must_not_be_called)
    response = upload(client, "policy.txt", b"Section 1. Scope")
    assert response.status_code == 500
    assert "nothing was saved" in response.json()["detail"]


def test_a_second_upload_while_one_is_being_indexed_is_refused_with_503(client, monkeypatch):
    monkeypatch.setattr(api, "insert_document", must_not_be_called)
    with api.UPLOAD_LOCK:  # as if another upload were being processed
        response = upload(client, "policy.txt", b"Section 1. Scope")
    assert response.status_code == 503
    assert "another upload" in response.json()["detail"]


def test_a_name_that_already_exists_is_409_before_anything_is_embedded(client, monkeypatch):
    monkeypatch.setattr(api, "document_exists", lambda filename: filename == "gdpr_ch3.txt")
    monkeypatch.setattr(api, "embed", must_not_be_called)
    monkeypatch.setattr(api, "insert_document", must_not_be_called)
    response = upload(client, "gdpr_ch3.txt", b"Art. 1 GDPR")
    assert response.status_code == 409
    assert response.json()["detail"] == "a document named gdpr_ch3.txt already exists"


def test_a_duplicate_that_passes_the_name_check_is_still_409_from_the_unique_constraint(client, monkeypatch):
    # Two uploads of one name at the same moment can both pass the check; Postgres refuses the second insert.
    def insert(**row):
        raise DuplicateDocument(row["filename"])

    monkeypatch.setattr(api, "insert_document", insert)
    monkeypatch.setattr(api, "index_document", must_not_be_called)
    response = upload(client, "gdpr_ch3.txt", b"Art. 1 GDPR")
    assert response.status_code == 409
    assert response.json()["detail"] == "a document named gdpr_ch3.txt already exists"


def test_if_indexing_fails_the_row_is_removed_and_the_upload_reports_500(client, monkeypatch, app_state):
    deleted = []

    def index(collection, doc, chunks, embeddings):
        raise RuntimeError("the index refused the chunks")

    monkeypatch.setattr(api, "insert_document", lambda **row: 41)
    monkeypatch.setattr(api, "index_document", index)
    monkeypatch.setattr(api, "delete_document", deleted.append)
    response = upload(client, "policy.txt", b"Section 1. Scope")
    assert response.status_code == 500
    assert "nothing was saved" in response.json()["detail"]
    assert deleted == [41]
    assert "policy.txt" not in app_state.indexed_documents


def test_a_model_outage_is_503_and_the_dashboard_can_read_it(client, monkeypatch):
    def outage(question, collection, client):
        raise ModelUnavailable("the model returned HTTP 503")

    monkeypatch.setattr(api, "answer_question", outage)
    response = client.post("/query", json={"question": "How fast must a breach be reported?"},
                           headers={"Origin": DASHBOARD})
    assert response.status_code == 503
    assert response.json() == {"detail": "model unavailable, please retry"}
    assert response.headers["access-control-allow-origin"] == DASHBOARD


def raise_a_bug(*args, **kwargs):
    raise RuntimeError("secret detail: a bug in our own code")


@pytest.mark.parametrize("method, path, broken", [
    ("POST", "/query", "answer_question"),
    ("GET", "/documents", "read_documents"),
])
def test_an_unexpected_error_on_any_route_is_a_logged_500_the_dashboard_can_read(client, monkeypatch, caplog,
                                                                                method, path, broken):
    monkeypatch.setattr(api, broken, raise_a_bug)
    response = client.request(method, path, json={"question": "How fast must a breach be reported?"},
                              headers={"Origin": DASHBOARD})
    assert response.status_code == 500
    assert response.json() == {"detail": "internal error"}  # never the exception's details
    assert response.headers["access-control-allow-origin"] == DASHBOARD
    assert "secret detail" in caplog.text  # the traceback is in the log instead


def test_health_is_ok_when_the_database_answers_and_no_document_failed(client):
    assert client.get("/health").json() == {"status": "ok", "database": True, "documents_indexed": 1,
                                            "chunks_indexed": 7, "failed_documents": []}


def test_health_is_degraded_but_still_200_when_the_database_is_down(client, monkeypatch):
    monkeypatch.setattr(api, "database_reachable", lambda: False)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["database"] is False


def test_health_is_degraded_and_names_a_document_that_failed_to_index(client, app_state):
    failure = {"filename": "lost.txt", "reason": "no stored content"}
    app_state.failed_documents = [failure]
    body = client.get("/health").json()
    assert body["status"] == "degraded"
    assert body["failed_documents"] == [failure]


@pytest.mark.parametrize("question", ["", "   "])
def test_an_empty_question_is_400_and_never_reaches_the_model(client, monkeypatch, question):
    monkeypatch.setattr(api, "answer_question", must_not_be_called)
    response = client.post("/query", json={"question": question})
    assert response.status_code == 400
    assert response.json() == {"detail": "question cannot be empty"}


def database_down(*args) -> None:
    raise OperationalError("SELECT ...", {}, Exception("connection refused"))


def test_startup_retries_the_database_while_it_wakes(monkeypatch):
    attempts, waits = [], []

    def answers_at_the_third_attempt() -> list[dict]:
        attempts.append(1)
        if len(attempts) < 3:
            database_down()
        return []

    monkeypatch.setattr(api, "read_documents_to_index", answers_at_the_third_attempt)
    monkeypatch.setattr(api.time, "sleep", waits.append)  # record each wait instead of waiting
    assert api.read_documents_with_retry() == []
    assert waits == [1, 2]


def test_startup_stops_if_the_database_never_answers(monkeypatch):
    waits = []
    monkeypatch.setattr(api, "read_documents_to_index", database_down)
    monkeypatch.setattr(api.time, "sleep", waits.append)
    with pytest.raises(OperationalError):  # startup fails, and Render restarts the service
        api.read_documents_with_retry()
    assert waits == [1, 2, 4, 8]


# From here on, the app on the test database (the `database` fixture in conftest.py): real database calls, a real
# in-memory index, a fake embedding model. Each test's writes are undone.

@pytest.fixture
def stored_client(database, monkeypatch) -> TestClient:
    state = State({"collection": new_index(), "client": None, "indexed_documents": set(), "failed_documents": []})
    monkeypatch.setattr(api.app, "state", state)
    monkeypatch.setattr(api, "embed", fake_embed)
    return TestClient(api.app)


def chunks_stored(database) -> int:
    return database.execute(text("SELECT count(*) FROM document_chunks")).scalar()


def listed(client: TestClient) -> dict:
    """The documents GET /documents lists, by filename."""
    return {d["filename"]: d for d in client.get("/documents").json()["documents"]}


def test_an_upload_is_stored_with_its_vectors_and_listed_unapproved(stored_client, database):
    assert upload(stored_client, "Retention Policy v2.txt", b"Section 1. Scope\nKeep records.").status_code == 200
    document = listed(stored_client)["Retention Policy v2.txt"]
    assert (document["approved"], document["retired"]) == (False, False)
    assert chunks_stored(database) == 1


def test_uploading_the_same_name_twice_is_409_and_keeps_the_first(stored_client, database):
    assert upload(stored_client, "policy.txt", b"Section 1. Scope").status_code == 200
    response = upload(stored_client, "policy.txt", b"Section 1. Another scope")
    assert response.status_code == 409
    assert response.json() == {"detail": "a document named policy.txt already exists"}
    assert [d["content"] for d in read_documents_to_index()] == ["Section 1. Scope"]
    assert chunks_stored(database) == 1


def test_a_duplicate_that_slips_past_the_name_check_is_409_from_postgres(stored_client, monkeypatch, database):
    assert upload(stored_client, "policy.txt", b"Section 1. Scope").status_code == 200
    monkeypatch.setattr(api, "document_exists", lambda filename: False)  # as when two uploads race
    response = upload(stored_client, "policy.txt", b"Section 1. Another scope")
    assert response.status_code == 409
    assert [d["content"] for d in read_documents_to_index()] == ["Section 1. Scope"]
    assert chunks_stored(database) == 1


def test_if_indexing_fails_the_row_and_its_chunks_are_removed(stored_client, monkeypatch, database):
    def index(collection, doc, chunks, embeddings):
        raise RuntimeError("the index refused the chunks")

    monkeypatch.setattr(api, "index_document", index)
    assert upload(stored_client, "policy.txt", b"Section 1. Scope").status_code == 500
    assert listed(stored_client) == {}
    assert chunks_stored(database) == 0


def test_approve_and_unapprove_change_the_listed_state(stored_client):
    name = "Retention Policy v2.txt"  # with spaces, sent encoded, as the dashboard's encodeURIComponent sends it
    assert upload(stored_client, name, b"Section 1. Scope").status_code == 200
    assert stored_client.post(f"/documents/{quote(name)}/approve").json() == {"filename": name, "approved": True}
    assert listed(stored_client)[name]["approved"] is True
    assert stored_client.post(f"/documents/{quote(name)}/unapprove").json() == {"filename": name, "approved": False}
    assert listed(stored_client)[name]["approved"] is False


def test_retire_marks_the_document_retired(stored_client):
    assert upload(stored_client, "policy.txt", b"Section 1. Scope").status_code == 200
    assert stored_client.post("/documents/policy.txt/retire").json() == {"filename": "policy.txt", "retired": True}
    assert listed(stored_client)["policy.txt"]["retired"] is True


@pytest.mark.parametrize("action", ["approve", "unapprove", "retire"])
def test_a_governance_change_to_an_unknown_document_is_404(stored_client, action):
    response = stored_client.post(f"/documents/nothing.txt/{action}")
    assert response.status_code == 404
    assert response.json() == {"detail": "document not found"}


POLICY = {"filename": "policy.txt", "owner": "Operations", "version": "1.0", "approved": True,
          "review_date": "2099-12-31"}  # far ahead, so the answer stays GOLD whatever today is
POLICY_TEXT = "Section 2. Breaches\nReport within 72 hours."
(POLICY_ID,), (POLICY_META,) = chunk_ids_and_metadata(POLICY, [POLICY_TEXT])


def test_an_answered_question_is_audited_with_the_chunk_it_used(stored_client):
    api.app.state.collection = FakeCollection([(POLICY_TEXT, POLICY_ID, 0.785, POLICY_META)])
    api.app.state.client = fake_gemini(lambda request: reply_with("Within 72 hours."))  # the real SDK, no network
    answered = stored_client.post("/query", json={"question": "How fast must a breach be reported?"}).json()
    assert (answered["answer"], answered["tier"]) == ("Within 72 hours.", "GOLD")
    entry = stored_client.get("/audit").json()["entries"][0]
    assert (entry["question"], entry["answer"], entry["tier"]) == (
        "How fast must a breach be reported?", "Within 72 hours.", "GOLD")
    assert entry["chunks"] == [{"chunk_id": POLICY_ID, "source": "policy.txt",
                                "content_hash": content_hash(POLICY_TEXT), "distance": 0.785}]
    assert entry["timestamp"].endswith("+00:00")


def test_a_question_with_no_relevant_source_is_audited_without_a_distance(stored_client):
    api.app.state.collection = FakeCollection([(POLICY_TEXT, POLICY_ID, 1.3, POLICY_META)])
    assert stored_client.post("/query", json={"question": "Who is our DPO?"}).status_code == 200
    entry = stored_client.get("/audit").json()["entries"][0]
    assert (entry["tier"], entry["top_distance"], entry["chunks"]) == ("BRONZE", None, [])


def test_health_reports_the_real_database_as_reachable(stored_client):
    assert stored_client.get("/health").json()["database"] is True
