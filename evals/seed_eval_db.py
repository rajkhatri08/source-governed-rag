"""Set the eval database to its fixed governance state: the three documents in metadata.json.

Run from the repo root: python -m evals.seed_eval_db
Safe to run again: it inserts or updates those three rows and never deletes anything.
"""
from evals.eval_db import use_eval_database

EVAL_HOST = use_eval_database()  # first: db.py reads DATABASE_URL when it is imported

import db  # noqa: E402

FIXED_DOCUMENTS = [
    {"filename": "gdpr_ch3.txt", "owner": "Legal & Compliance", "version": "2016/679",
     "approved": True, "review_date": "2027-05-25"},
    {"filename": "retention_policy_v1.txt", "owner": "Operations", "version": "1.0",
     "approved": True, "review_date": "2023-01-15"},
    {"filename": "draft_ai_policy.txt", "owner": "Technology Risk", "version": "0.3",
     "approved": False, "review_date": "2027-12-31"},
]


def main() -> None:
    print(f"eval database host: {EVAL_HOST}")
    db.init_db()  # creates the tables in the new eval database; existing tables are left alone

    session = db.Session()
    try:
        wanted = {d["filename"] for d in FIXED_DOCUMENTS}
        others = [row.filename for row in session.query(db.Document).all() if row.filename not in wanted]
        if others:
            raise SystemExit(f"the eval database has documents outside the fixed state: {others}; "
                             "remove them by hand, then run this again")
        for fixed in FIXED_DOCUMENTS:
            row = session.query(db.Document).filter(db.Document.filename == fixed["filename"]).first()
            if row is None:
                row = db.Document(filename=fixed["filename"])
                session.add(row)
            row.owner = fixed["owner"]
            row.version = fixed["version"]
            row.approved = fixed["approved"]
            row.retired = False
            row.review_date = fixed["review_date"]
        session.commit()
    finally:
        session.close()

    for d in db.read_documents():
        print(f"  {d['filename']}: approved={d['approved']} retired={d['retired']} review={d['review_date']}")


if __name__ == "__main__":
    main()
