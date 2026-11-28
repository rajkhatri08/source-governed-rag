from datetime import date

import pytest

from generate import PROMPT_TEMPLATE
from govern import (RELEVANCE_CUTOFF, REFUSAL_SENTINEL, classify, is_approved, is_expired, is_refusal,
                    source_warnings)

TODAY = date(2026, 11, 18)
GDPR = {"source": "gdpr_ch3.txt", "approved": True, "review_date": "2027-05-25"}
RETENTION = {"source": "retention_policy_v1.txt", "approved": True, "review_date": "2023-01-15"}  # past review
DRAFT = {"source": "draft_ai_policy.txt", "approved": False, "review_date": "2027-12-31"}
UNDATED = {"source": "undated_policy.txt", "approved": True, "review_date": None}  # approved, no valid review date
UNREADABLE_DATES = [None, "", "next year", "2027-02-30"]


@pytest.mark.parametrize("reply", [
    "NO_ANSWER_IN_CONTEXT",
    "  NO_ANSWER_IN_CONTEXT\n",
    "NO_ANSWER_IN_CONTEXT.",
    "Based on the provided context, NO_ANSWER_IN_CONTEXT",
    "NO_ANSWER_IN_CONTEXT. The context covers breach notification, not fines.",
    "",
    " \n",
])
def test_the_sentinel_anywhere_in_the_reply_or_an_empty_reply_is_a_refusal(reply):
    assert is_refusal(reply)


@pytest.mark.parametrize("reply", [
    "Within 72 hours.",
    "I don't know why the regulator chose 72 hours, but Art. 33 requires notification within them.",
])
def test_an_answer_is_not_a_refusal_even_when_it_says_dont_know(reply):
    assert not is_refusal(reply)


def test_the_prompt_asks_for_the_sentinel_that_is_refusal_checks():
    assert REFUSAL_SENTINEL in PROMPT_TEMPLATE


def test_a_review_date_counts_as_passed_only_once_it_is_before_today():
    assert is_expired("2026-11-17", TODAY)
    assert not is_expired("2026-11-18", TODAY)


@pytest.mark.parametrize("review_date", UNREADABLE_DATES)
def test_a_missing_or_unreadable_review_date_counts_as_passed(review_date):
    assert is_expired(review_date, TODAY)


@pytest.mark.parametrize("meta", [{"approved": False}, {"approved": None}, {}, {"approved": 1}, {"approved": "true"}])
def test_only_exactly_true_is_approved(meta):
    assert not is_approved(meta)


@pytest.mark.parametrize("meta", [{**GDPR, "approved": None}, {"source": "gdpr_ch3.txt", "review_date": "2027-05-25"}])
def test_a_source_with_unknown_approval_makes_the_answer_bronze(meta):
    assert classify([meta], [0.8], "Within 72 hours.", TODAY) == ("BRONZE", "source not approved: gdpr_ch3.txt")


@pytest.mark.parametrize("review_date", UNREADABLE_DATES)
def test_a_source_without_a_valid_review_date_is_silver_and_the_reason_says_why(review_date):
    tier, reason = classify([{**GDPR, "review_date": review_date}], [0.8], "Within 72 hours.", TODAY)
    assert tier == "SILVER"
    assert reason == "source has no valid review date, so it counts as past review: gdpr_ch3.txt"


def test_a_source_past_its_review_date_is_silver():
    assert classify([RETENTION], [0.9], "Within 30 days.", TODAY) == (
        "SILVER", "source past review date: retention_policy_v1.txt")


def test_an_approved_current_source_is_gold():
    assert classify([GDPR], [0.8], "Within 72 hours.", TODAY) == (
        "GOLD", "answered from an approved, current source")


def test_a_refusal_is_bronze():
    assert classify([GDPR], [0.8], "NO_ANSWER_IN_CONTEXT", TODAY) == (
        "BRONZE", "no answer found in the available sources")


# The order of the rules: refusal, then no relevant source, then approval, then review date.

@pytest.mark.parametrize("source", [DRAFT, RETENTION, UNDATED])
def test_a_refusal_comes_first_whatever_the_state_of_its_sources(source):
    assert classify([source], [0.8], "NO_ANSWER_IN_CONTEXT", TODAY) == (
        "BRONZE", "no answer found in the available sources")


@pytest.mark.parametrize("metas, distances", [([], []), ([DRAFT], [1.3]), ([GDPR], [RELEVANCE_CUTOFF])])
def test_with_no_source_under_the_cutoff_the_answer_is_bronze_never_gold(metas, distances):
    assert classify(metas, distances, "Within 72 hours.", TODAY) == ("BRONZE", "no relevant source in the corpus")


@pytest.mark.parametrize("metas", [[RETENTION, DRAFT], [DRAFT, RETENTION], [UNDATED, DRAFT]])
def test_an_unapproved_source_outranks_any_review_date_problem_in_either_order(metas):
    assert classify(metas, [0.7, 0.9], "An answer.", TODAY) == ("BRONZE", "source not approved: draft_ai_policy.txt")


def test_one_unapproved_source_makes_an_answer_from_an_approved_current_one_bronze():
    assert classify([GDPR, DRAFT], [0.7, 0.9], "Within 72 hours.", TODAY) == (
        "BRONZE", "source not approved: draft_ai_policy.txt")


def test_one_source_past_review_makes_an_answer_from_an_approved_current_one_silver():
    assert classify([GDPR, RETENTION], [0.7, 0.9], "Within 72 hours.", TODAY) == (
        "SILVER", "source past review date: retention_policy_v1.txt")


def test_of_two_review_date_problems_the_source_retrieved_first_gives_the_reason():
    # Both are SILVER; the warnings name every problem, the reason names the first source with one
    assert classify([RETENTION, UNDATED], [0.7, 0.9], "An answer.", TODAY) == (
        "SILVER", "source past review date: retention_policy_v1.txt")
    assert classify([UNDATED, RETENTION], [0.7, 0.9], "An answer.", TODAY) == (
        "SILVER", "source has no valid review date, so it counts as past review: undated_policy.txt")


def test_a_source_at_the_cutoff_takes_no_part_in_the_tier():
    # The filter keeps distances strictly below the cutoff: an unapproved source at exactly 1.2 can't veto
    assert classify([GDPR, DRAFT], [0.8, RELEVANCE_CUTOFF], "Within 72 hours.", TODAY) == (
        "GOLD", "answered from an approved, current source")


def test_a_source_with_several_chunks_gets_each_warning_once():
    assert source_warnings([RETENTION, RETENTION, RETENTION], TODAY) == ["expired source: retention_policy_v1.txt"]


def test_warnings_keep_the_order_in_which_their_sources_were_retrieved():
    assert source_warnings([DRAFT, RETENTION, DRAFT, GDPR], TODAY) == [
        "unapproved source: draft_ai_policy.txt", "expired source: retention_policy_v1.txt"]


def test_unknown_approval_and_an_unreadable_review_date_both_warn():
    meta = {"source": "upload.txt", "approved": None, "review_date": "soon"}
    assert source_warnings([meta], TODAY) == ["no valid review date: upload.txt", "unapproved source: upload.txt"]
