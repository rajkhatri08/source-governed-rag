"""Set the eval database to its fixed state: the three documents in metadata.json, with their text.

Run from the repo root: python -m evals.seed_eval_db
Safe to run again: it inserts or updates those three rows and never deletes anything. Each row's
content is extracted from its file in documents/ exactly as an upload's would be, so the eval
indexes the same text as the app.
The tables come from the migrations, not from this script: run `alembic -x db=eval upgrade head` first.
"""
from pathlib import Path

from evals.eval_db import use_eval_database

EVAL_HOST = use_eval_database()  # first: db.py reads DATABASE_URL when it is imported

import db  # noqa: E402
from retrieve import content_hash  # noqa: E402
from upload import extract_text  # noqa: E402

DOCUMENTS = Path("documents")

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
            row.content = extract_text(fixed["filename"], (DOCUMENTS / fixed["filename"]).read_bytes())
            row.content_sha256 = content_hash(row.content)
        session.commit()
    finally:
        session.close()

    for d in db.read_documents_to_index():  # read back from the database, so this checks what was stored
        print(f"  {d['filename']}: approved={d['approved']} review={d['review_date']} "
              f"content={len(d['content'])} characters, sha256 {content_hash(d['content'])[:12]}")


if __name__ == "__main__":
    main()
