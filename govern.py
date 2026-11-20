from datetime import date

RELEVANCE_CUTOFF = 1.2


def is_expired(review_date: str, today: date | None = None) -> bool:
    """True if the review date has passed; `today` defaults to the real date (the eval fixes it)."""
    if today is None:
        today = date.today()
    return date.fromisoformat(review_date) < today


def is_refusal(answer: str) -> bool:
    """True if the model said the context doesn't contain the answer."""
    return "don't know" in answer.lower()


def classify(metas: list[dict], distances: list[float], answer: str,
             today: date | None = None) -> tuple[str, str]:
    """The tier and its reason, from the ordered rules: refusal, then approval, then review date."""
    if is_refusal(answer):
        return "BRONZE", "no answer found in the available sources"

    relevant = []
    for meta, distance in zip(metas, distances):
        if distance < RELEVANCE_CUTOFF:
            relevant.append(meta)

    for m in relevant:
        if m["approved"] == False:
            return "BRONZE", "source not approved: " + m["source"]

    for m in relevant:
        if is_expired(m["review_date"], today):
            return "SILVER", "source past review date: " + m["source"]

    return "GOLD", "answered from an approved, current source"
