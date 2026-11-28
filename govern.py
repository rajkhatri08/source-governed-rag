from datetime import date

RELEVANCE_CUTOFF = 1.2
# What the prompt (generate.py) tells the model to reply when the context doesn't hold the answer. A token no real
# answer contains, unlike "I don't know", which also appears in answers ("I don't know why the regulator…").
REFUSAL_SENTINEL = "NO_ANSWER_IN_CONTEXT"
NO_RELEVANT_SOURCE = "no relevant source in the corpus"  # the reason when no chunk passes the relevance filter


def parse_review_date(review_date: str | None) -> date | None:
    """The review date as a date, or None if it is missing or isn't a real ISO date such as 2027-05-25."""
    try:
        return date.fromisoformat(review_date)
    except (TypeError, ValueError):  # TypeError: None, or not a string; ValueError: not a real date
        return None


def is_expired(review_date: str | None, today: date | None = None) -> bool:
    """True if the review date has passed, or is missing or unreadable (fail closed).

    `today` defaults to the real date (the eval fixes it).
    """
    if today is None:
        today = date.today()
    review = parse_review_date(review_date)
    return review is None or review < today


def is_approved(meta: dict) -> bool:
    """Approved means exactly True: False, None (a NULL in Postgres) or no value at all mean unapproved (fail closed).

    `.get`, not `meta["approved"]`, so that a missing value is unapproved rather than a crash.
    """
    return meta.get("approved") is True


def is_refusal(reply: str) -> bool:
    """True if the model refused: its reply holds the sentinel anywhere, or holds no text at all.

    Anywhere, not only alone: the model sometimes adds an explanation after it or a preamble before it ("Based on
    the provided context, …"). A reply that mixes a partial answer with the sentinel is a refusal too: fail closed.
    """
    return not reply.strip() or REFUSAL_SENTINEL in reply


def source_warnings(metas: list[dict], today: date | None = None) -> list[str]:
    """One warning per source and problem, in the order the sources were retrieved."""
    warnings = []
    for m in metas:
        review_date = m.get("review_date")
        if parse_review_date(review_date) is None:
            warnings.append("no valid review date: " + m["source"])
        elif is_expired(review_date, today):
            warnings.append("expired source: " + m["source"])
        if not is_approved(m):
            warnings.append("unapproved source: " + m["source"])
    # A source with several chunks in the top 5 would otherwise repeat its warnings. dict.fromkeys keeps the first
    # of each, in order; a set would drop the repeats too, but its order changes from one run to the next.
    return list(dict.fromkeys(warnings))


def classify(metas: list[dict], distances: list[float], reply: str,
             today: date | None = None) -> tuple[str, str]:
    """The tier and its reason, from the ordered rules: refusal, no relevant source, approval, review date."""
    if is_refusal(reply):
        return "BRONZE", "no answer found in the available sources"

    relevant = []
    for meta, distance in zip(metas, distances):
        if distance < RELEVANCE_CUTOFF:
            relevant.append(meta)
    # Without this, an answer with no relevant source would pass every rule below and come out GOLD (fail closed).
    # answer_question never asks with no relevant source, but classify mustn't depend on its caller for that.
    if not relevant:
        return "BRONZE", NO_RELEVANT_SOURCE

    for m in relevant:
        if not is_approved(m):
            return "BRONZE", "source not approved: " + m["source"]

    for m in relevant:
        review_date = m.get("review_date")
        if parse_review_date(review_date) is None:
            return "SILVER", "source has no valid review date, so it counts as past review: " + m["source"]
        if is_expired(review_date, today):
            return "SILVER", "source past review date: " + m["source"]

    return "GOLD", "answered from an approved, current source"
