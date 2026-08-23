from datetime import date


def is_expired(review_date):
    return date.fromisoformat(review_date) < date.today()


def classify(distance, metas):
    for m in metas:
        if m["approved"] == False:
            return "BRONZE"

    for m in metas:
        if is_expired(m["review_date"]):
            return "SILVER"

    if distance > 1.1:
        return "BRONZE"

    if distance > 0.95:
        return "SILVER"

    return "GOLD"