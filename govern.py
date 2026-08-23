from datetime import date


def is_expired(review_date):
    return date.fromisoformat(review_date) < date.today()


def classify(distance, metas, distances):
    relevant = []
    for i in range(len(metas)):
        if distances[i] < 1.2:
            relevant.append(metas[i])

    for m in relevant:
        if m["approved"] == False:
            return "BRONZE"

    for m in relevant:
        if is_expired(m["review_date"]):
            return "SILVER"

    if distance > 1.1:
        return "BRONZE"

    if distance > 0.95:
        return "SILVER"

    return "GOLD"