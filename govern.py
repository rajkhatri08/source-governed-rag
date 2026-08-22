from datetime import date


def is_expired(review_date):
    return date.fromisoformat(review_date) < date.today()