from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

import db
from db import is_unique_violation
from pipeline import chunk_rows
from retrieve import EMBEDDING_MODEL, content_hash, make_chunk_id
from testdb import check_test_url

TEXTS = ["Section 1. Scope\nApplies to staff.", "Section 2. Breaches\nReport within 72 hours."]
VECTORS = [[0.5, 0.25], [0.125, -1.0]]  # exactly representable in a 4-byte REAL, so they read back unchanged


class PostgresError(Exception):
    """What psycopg2 attaches to a failed statement: the SQLSTATE code and the table Postgres names."""

    def __init__(self, pgcode: str, table_name: str):
        super().__init__(pgcode)
        self.pgcode = pgcode
        self.diag = SimpleNamespace(table_name=table_name)


def integrity_error(pgcode: str, table_name: str) -> IntegrityError:
    return IntegrityError("INSERT INTO documents ...", {}, PostgresError(pgcode, table_name))


def test_a_duplicate_is_recognised_by_its_sqlstate_and_table_not_by_a_constraint_name():
    assert is_unique_violation(integrity_error("23505", "documents"), "documents")


def test_a_duplicate_in_another_table_or_a_different_violation_is_not_a_duplicate_document():
    assert not is_unique_violation(integrity_error("23505", "audit_log"), "documents")
    assert not is_unique_violation(integrity_error("23502", "documents"), "documents")  # 23502: a NOT NULL violation


@pytest.mark.parametrize("url", [
    "postgresql://user@ep-example.ap-southeast-1.aws.neon.tech/compliance_test",  # Neon is production
    "postgresql://localhost:5432/compliance",  # the development database
    "postgresql://localhost:5432/compliance_eval",
])
def test_the_test_database_check_refuses_neon_and_every_other_database(url):
    with pytest.raises(SystemExit):
        check_test_url(url)


def test_the_test_database_check_accepts_a_local_compliance_test():
    check_test_url("postgresql://postgres@localhost:5432/compliance_test")


# From here on, against the test database (the `database` fixture in conftest.py): each test's writes are undone.

def store(filename: str = "policy.txt") -> int:
    """Insert one document with its two chunks and their vectors, as an upload does; returns its id."""
    content = "\n".join(TEXTS)
    return db.insert_document(filename=filename, owner="Operations", version="1.0", review_date="2027-01-15",
                              content=content, content_sha256=content_hash(content),
                              chunks=chunk_rows(filename, TEXTS, VECTORS))


def chunks_stored(database) -> int:
    return database.execute(text("SELECT count(*) FROM document_chunks")).scalar()


def test_a_stored_document_comes_back_for_indexing_unapproved_with_its_text(database):
    document_id = store()
    assert db.read_documents_to_index() == [{"id": document_id, "filename": "policy.txt", "owner": "Operations",
                                             "version": "1.0", "approved": False, "review_date": "2027-01-15",
                                             "content": "\n".join(TEXTS)}]


def test_stored_vectors_are_read_back_for_one_document_and_one_model(database):
    document_id = store()
    store("other.txt")
    assert db.read_stored_vectors(document_id, EMBEDDING_MODEL) == {
        make_chunk_id("policy.txt", index, chunk): (content_hash(chunk), vector)
        for index, (chunk, vector) in enumerate(zip(TEXTS, VECTORS))}
    assert db.read_stored_vectors(document_id, "another model") == {}


def test_a_second_document_with_the_same_name_is_refused_by_postgres_and_the_first_is_kept(database):
    document_id = store()
    with pytest.raises(db.DuplicateDocument):
        store()  # no name check first: the unique constraint alone decides, as when two uploads race
    assert len(db.read_stored_vectors(document_id, EMBEDDING_MODEL)) == 2
    assert chunks_stored(database) == 2


def test_a_chunk_that_cant_be_stored_leaves_no_document_behind(database):
    rows = chunk_rows("policy.txt", TEXTS, VECTORS)
    rows[1]["chunk_id"] = rows[0]["chunk_id"]  # two chunks with one id: refused by document_chunks' unique key
    with pytest.raises(IntegrityError):  # a plain IntegrityError: the duplicate isn't in documents
        db.insert_document(filename="policy.txt", owner="Operations", version="1.0", review_date="2027-01-15",
                           content="\n".join(TEXTS), content_sha256=content_hash("\n".join(TEXTS)), chunks=rows)
    assert not db.document_exists("policy.txt")  # one transaction: the document's row went with its chunks


def test_deleting_a_document_deletes_its_chunks_with_it(database):
    document_id = store()
    db.delete_document(document_id)
    assert not db.document_exists("policy.txt")
    assert chunks_stored(database) == 0  # ON DELETE CASCADE


def test_a_retired_document_is_not_indexed_but_its_name_stays_taken(database):
    store()
    assert db.set_retired("policy.txt", True)
    assert db.read_documents_to_index() == []
    assert db.document_exists("policy.txt")


def test_a_governance_change_to_an_unknown_document_reports_that_it_found_nothing(database):
    assert not db.set_approved("nothing.txt", True)
    assert not db.set_retired("nothing.txt", True)


def test_the_audit_log_reads_back_newest_first_in_utc_with_no_distance_when_nothing_passed(database):
    chunk = {"chunk_id": make_chunk_id("policy.txt", 1, TEXTS[1]), "source": "policy.txt",
             "content_hash": content_hash(TEXTS[1]), "distance": 0.785}
    db.log_query_db("How fast must a breach be reported?", answer="Within 72 hours.", tier="SILVER",
                    reason="source past review date: policy.txt", chunks=[chunk],
                    warnings=["expired source: policy.txt"])
    db.log_query_db("Who is our DPO?", answer="No sufficiently relevant source found.", tier="BRONZE",
                    reason="no relevant source in the corpus", chunks=[], warnings=[])
    newest, older = db.read_log_db()
    assert (newest["question"], newest["top_distance"], newest["chunks"]) == ("Who is our DPO?", None, [])
    assert (older["top_distance"], older["chunks"], older["warnings"]) == (
        0.785, [chunk], ["expired source: policy.txt"])
    assert older["timestamp"].endswith("+00:00")


def test_the_test_database_is_reachable(database):
    assert db.database_reachable()
