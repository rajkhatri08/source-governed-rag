import pytest

from evals.eval_db import check_eval_url
from evals.scoring import cache_key, missing_facts, missing_sources, score_question, summarise, was_refused

QUESTION = {
    "group": "g",
    "answerable": True,
    "expected_sources": [("gdpr_ch3.txt", "Art. 33 GDPR")],
    "expected_facts": [["72 hours", "seventy-two hours"]],
    "expected_tier": "BRONZE",
}
ANSWERED = {"answer": "Within 72 hours.", "tier": "BRONZE", "refused": False, "sources": ["chunk_5"],
            "distances": [0.8]}
ART_33_CHUNK = {"source": "gdpr_ch3.txt", "text": "Art. 33 GDPR\nNotification of a personal data breach"}


def test_facts_ignore_case_and_accept_any_alternative():
    assert missing_facts("Notify within Seventy-Two  Hours.", [["72 hours", "seventy-two hours"]]) == []


def test_every_fact_list_must_match():
    assert missing_facts("You can learn the purposes.", [["purposes"], ["recipients"]]) == [["recipients"]]


def test_the_refusal_score_reads_the_systems_own_flag():
    assert was_refused({"answer": "No sufficiently relevant source found.", "sources": [], "refused": True})
    assert was_refused({"answer": "The sources found don't contain the answer to this question.",
                        "sources": ["chunk_1"], "refused": True})
    assert not was_refused({"answer": "I don't know why the regulator chose it, but within 72 hours.",
                            "sources": ["chunk_1"], "refused": False})


def test_a_source_needs_both_its_document_and_its_heading():
    assert missing_sources([ART_33_CHUNK], [("gdpr_ch3.txt", "Art. 33 GDPR")]) == []
    assert missing_sources([ART_33_CHUNK], [("gdpr_ch3.txt", "Art. 12 GDPR")]) == [["gdpr_ch3.txt", "Art. 12 GDPR"]]
    assert missing_sources([ART_33_CHUNK], [("draft_ai_policy.txt", "Art. 33 GDPR")]) != []


def test_a_correct_answer_scores_on_every_metric():
    scores = score_question(QUESTION, ANSWERED, [ART_33_CHUNK])
    assert scores["refusal"] and scores["facts"] and scores["tier"] and scores["sources"]


def test_a_failed_question_stays_in_the_denominator_and_is_never_wrong():
    rows = [
        {**QUESTION, "scores": score_question(QUESTION, None, [])},
        {**QUESTION, "scores": score_question(QUESTION, ANSWERED, [ART_33_CHUNK])},
    ]
    tier = summarise(rows)["g"]["tier"]
    assert tier == {"correct": 1, "wrong": 0, "failed": 1, "n": 2}


def test_cache_key_changes_when_any_input_changes():
    base = {"question": "q", "chunk_ids": ["chunk_1"], "chunk_texts": ["text"],
            "prompt_template": "{context} {question}", "model": "m", "generation_config": {}}
    changes = {"question": "q2", "chunk_ids": ["chunk_2"], "chunk_texts": ["other text"],
               "prompt_template": "{question} {context}", "model": "m2",
               "generation_config": {"thinking_config": {"thinking_level": "MINIMAL"}}}
    for field, value in changes.items():
        assert cache_key(**{**base, field: value}) != cache_key(**base), field


@pytest.mark.parametrize("url", [
    None,
    "",
    "postgresql://user:password@ep-example.neon.tech/compliance_eval",
    "postgresql://user:password@localhost:5432/compliance",
])
def test_eval_database_check_refuses_unsafe_urls(url):
    with pytest.raises(SystemExit):
        check_eval_url(url)


def test_eval_database_check_accepts_the_local_eval_database():
    check_eval_url("postgresql://user:password@localhost:5432/compliance_eval")
