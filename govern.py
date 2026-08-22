import json
from datetime import date, datetime



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
def log_query(question, tier, sources, distances, warnings):
    entry = {
        "timestamp": datetime.now().isoformat(),
        "question": question,
        "tier": tier,
        "sources": sources,
        "top_distance": distances[0],
        "warnings": warnings
    }

    try:
        with open("audit_log.json", "r") as f:
            log = json.load(f)
    except:
        log = []

    log.append(entry)

    with open("audit_log.json", "w") as f:
        json.dump(log, f, indent=2)

    return entry


def read_log():
    try:
        with open("audit_log.json", "r") as f:
            return json.load(f)
    except:
        return []