import re

from pipeline import chunk_ids_and_metadata
from retrieve import content_hash, make_chunk_id

DOC = {"filename": "policy.txt", "owner": "Operations", "version": "1.0",
       "approved": True, "review_date": "2027-01-01"}


def test_the_content_hash_is_the_sha256_of_the_text():
    # The standard SHA-256 test value: anyone can check a hash with `shasum -a 256`.
    assert content_hash("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_the_same_chunk_always_gets_the_same_id():
    assert make_chunk_id("policy.txt", 0, "Section 1. Scope") == make_chunk_id("policy.txt", 0, "Section 1. Scope")


def test_changed_text_gives_a_different_id():
    assert make_chunk_id("policy.txt", 0, "within 30 days") != make_chunk_id("policy.txt", 0, "within 60 days")


def test_the_position_and_the_file_are_part_of_the_id():
    text = "Section 1. Scope"
    ids = {make_chunk_id("policy.txt", 0, text), make_chunk_id("policy.txt", 1, text),
           make_chunk_id("other.txt", 0, text)}
    assert len(ids) == 3


def test_the_id_names_the_file_the_position_and_the_start_of_the_content_hash():
    text = "Section 2. Breach Notification"
    assert re.fullmatch(r"policy\.txt::2::[0-9a-f]{12}", make_chunk_id("policy.txt", 2, text))
    assert make_chunk_id("policy.txt", 2, text).endswith(content_hash(text)[:12])


def test_each_chunk_gets_its_id_position_hash_and_the_documents_governance_fields():
    ids, metadatas = chunk_ids_and_metadata(DOC, ["first chunk", "second chunk"])
    assert ids == [make_chunk_id("policy.txt", 0, "first chunk"), make_chunk_id("policy.txt", 1, "second chunk")]
    assert metadatas[1] == {"source": "policy.txt", "owner": "Operations", "version": "1.0",
                            "approved": True, "review_date": "2027-01-01",
                            "chunk_index": 1, "content_hash": content_hash("second chunk")}
