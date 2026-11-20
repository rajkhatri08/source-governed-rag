import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import State

import api
from db import DuplicateDocument
from pdf_samples import blank_pdf
from upload import MAX_UPLOAD_BYTES

FIELDS = {"owner": "Operations", "version": "1.0", "review_date": "2027-01-15"}


class FakeIndex:
    """Stands in for the Chroma collection that startup would have built."""

    def count(self) -> int:
        return 7


@pytest.fixture
def app_state(monkeypatch) -> State:
    """What startup would have put on app.state, made by hand: these tests never run the real startup.

    A fresh State for each test, swapped in whole: a test can change it freely, and the app gets
    its own back afterwards.
    """
    state = State({"collection": FakeIndex(), "indexed_documents": {"gdpr_ch3.txt"}, "failed_documents": []})
    monkeypatch.setattr(api.app, "state", state)
    monkeypatch.setattr(api, "database_reachable", lambda: True)
    return state


@pytest.fixture
def client(app_state) -> TestClient:
    return TestClient(api.app)  # not `with TestClient(...)`, which would run the startup


def upload(client: TestClient, name: str, data: bytes, **fields):
    return client.post("/upload", files={"file": (name, data)}, data={**FIELDS, **fields})


def must_not_be_called(*args, **kwargs):
    raise AssertionError("a rejected upload reached the database or the index")


@pytest.mark.parametrize("name, data, fields, status", [
    ("../x.txt", b"text", {}, 400),
    (".env", b"KEY=value", {}, 400),
    ("a.exe", b"MZ", {}, 400),
    ("policy.txt", b"text", {"review_date": "next year"}, 400),
    ("big.txt", b"a" * (MAX_UPLOAD_BYTES + 1), {}, 413),
    ("scan.pdf", blank_pdf(), {}, 422),
])
def test_a_rejected_upload_never_reaches_the_database_or_the_index(client, monkeypatch, name, data, fields, status):
    monkeypatch.setattr(api, "insert_document", must_not_be_called)
    monkeypatch.setattr(api, "index_document", must_not_be_called)
    response = upload(client, name, data, **fields)
    assert response.status_code == status
    assert isinstance(response.json()["detail"], str)


def test_an_accepted_upload_is_stored_with_its_text_then_indexed_unapproved(client, monkeypatch, app_state):
    stored, indexed = [], []

    def insert(**row):
        stored.append(row)
        return 41

    monkeypatch.setattr(api, "insert_document", insert)
    monkeypatch.setattr(api, "index_document", lambda collection, doc, chunks: indexed.append((doc, chunks)))
    response = upload(client, "Retention Policy v2.txt", b"Section 1. Scope\nKeep records for 6 years.")
    assert response.status_code == 200
    assert response.json() == {"filename": "Retention Policy v2.txt", "chunks": 1, "approved": False}
    assert stored[0]["content"] == "Section 1. Scope\nKeep records for 6 years."
    assert indexed[0][0]["approved"] is False
    assert "Retention Policy v2.txt" in app_state.indexed_documents


def test_a_duplicate_name_is_409_and_nothing_is_indexed(client, monkeypatch):
    def insert(**row):
        raise DuplicateDocument(row["filename"])

    monkeypatch.setattr(api, "insert_document", insert)
    monkeypatch.setattr(api, "index_document", must_not_be_called)
    response = upload(client, "gdpr_ch3.txt", b"Art. 1 GDPR")
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


def test_if_indexing_fails_the_row_is_removed_and_the_upload_reports_500(client, monkeypatch, app_state):
    deleted = []

    def index(collection, doc, chunks):
        raise RuntimeError("embedding model unavailable")

    monkeypatch.setattr(api, "insert_document", lambda **row: 41)
    monkeypatch.setattr(api, "index_document", index)
    monkeypatch.setattr(api, "delete_document", deleted.append)
    response = upload(client, "policy.txt", b"Section 1. Scope")
    assert response.status_code == 500
    assert "nothing was saved" in response.json()["detail"]
    assert deleted == [41]
    assert "policy.txt" not in app_state.indexed_documents


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
