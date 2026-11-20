import json
from datetime import datetime, timedelta, timezone

from db import AuditEntry, audit_entry_to_dict, new_audit_entry

IST = timezone(timedelta(hours=5, minutes=30))
CHUNKS = [
    {"chunk_id": "gdpr_ch3.txt::15::aaaaaaaaaaaa", "source": "gdpr_ch3.txt",
     "content_hash": "a" * 64, "distance": 0.785},
    {"chunk_id": "retention_policy_v1.txt::1::bbbbbbbbbbbb", "source": "retention_policy_v1.txt",
     "content_hash": "b" * 64, "distance": 0.930},
]


def entry(chunks: list[dict]) -> AuditEntry:
    return new_audit_entry("How fast must a breach be reported?", answer="Within 72 hours.", tier="SILVER",
                           reason="source past review date: retention_policy_v1.txt", chunks=chunks, warnings=[])


def test_no_passing_chunk_records_no_distance_rather_than_zero():
    row = entry([])
    assert row.top_distance is None
    assert json.loads(row.sources) == []
    assert json.loads(row.chunks) == []


def test_the_row_records_the_answer_each_chunk_used_and_the_best_distance():
    row = entry(CHUNKS)
    assert row.answer == "Within 72 hours."
    assert row.top_distance == 0.785
    assert json.loads(row.sources) == ["gdpr_ch3.txt::15::aaaaaaaaaaaa", "retention_policy_v1.txt::1::bbbbbbbbbbbb"]
    assert json.loads(row.chunks) == CHUNKS


def test_a_new_row_is_stamped_in_utc():
    assert entry([]).created_at.utcoffset() == timedelta(0)


def test_created_at_is_reported_in_utc_whatever_zone_postgres_returns_it_in():
    row = entry(CHUNKS)
    row.created_at = datetime(2026, 11, 20, 17, 15, tzinfo=IST)  # how local Postgres (Asia/Kolkata) hands it back
    assert audit_entry_to_dict(row)["timestamp"] == "2026-11-20T11:45:00+00:00"


def test_a_row_from_before_p0_2_reads_with_null_answer_and_chunks():
    old = AuditEntry(id=1, timestamp=datetime(2026, 8, 30, 21, 5), question="Who is the DPO?", tier="BRONZE",
                     reason="no relevant source in the corpus", top_distance=0.0, sources="[]", warnings="[]")
    read = audit_entry_to_dict(old)
    assert read["answer"] is None
    assert read["chunks"] is None
    assert read["timestamp"] == "2026-08-30T21:05:00"  # no offset: the zone it was written in is unknown
    assert read["top_distance"] == 0.0  # old rows keep what they recorded
