from datetime import date


def is_expired(review_date):
    return date.fromisoformat(review_date) < date.today()


def classify(metas, distances, answer):
    if "don't know" in answer.lower():
        return "BRONZE", "no answer found in the available sources"

    relevant = []
    for i in range(len(metas)):
        if distances[i] < 1.2:
            relevant.append(metas[i])

    for m in relevant:
        if m["approved"] == False:
            return "BRONZE", "source not approved: " + m["source"]

    for m in relevant:
        if is_expired(m["review_date"]):
            return "SILVER", "source past review date: " + m["source"]

    return "GOLD", "answered from an approved, current source"