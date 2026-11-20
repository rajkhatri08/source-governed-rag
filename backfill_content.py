"""Fill documents.content for rows that have none, from the seed files in documents/.

Run from the repo root:
    python backfill_content.py --dry-run              report what it would do; change nothing
    python backfill_content.py                        fill every row it can
    python backfill_content.py --retire-unfillable    also retire the rows it can't fill

DATABASE_URL comes from the shell if it is set there, else .env (Ship 1: DATABASE_URL='<Neon URL>' ...).
Idempotent: a row that already has content is left alone, so a second run changes nothing. The text
is extracted exactly as an upload's would be (upload.extract_text). Everything happens in one
transaction: if anything goes wrong, nothing is changed.
"""
import argparse
from pathlib import Path
from urllib.parse import urlparse

import db
from retrieve import content_hash
from upload import UploadRejected, extract_text

DOCUMENTS = Path("documents")


def seed_file(filename: str) -> Path | None:
    """The file in documents/ that this row names, or None if the name points anywhere else.

    Rows written before H0's name check could be called "../.env"; reading that would store a
    secret as a document.
    """
    path = (DOCUMENTS / filename).resolve()
    return path if path.parent == DOCUMENTS.resolve() else None


def text_for(filename: str) -> tuple[str | None, str]:
    """The row's text and "", or None and the reason it can't be filled."""
    path = seed_file(filename)
    if path is None:
        return None, "the name points outside documents/"
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None, "no such file in documents/"
    except OSError as e:
        return None, f"the file can't be read ({type(e).__name__})"
    try:
        return extract_text(filename, data), ""
    except UploadRejected as e:
        return None, e.message


def main() -> None:
    parser = argparse.ArgumentParser(description="Fill documents.content from the seed files in documents/.")
    parser.add_argument("--dry-run", action="store_true", help="report what would change, then roll back")
    parser.add_argument("--retire-unfillable", action="store_true", help="retire the rows that can't be filled")
    args = parser.parse_args()

    parsed = urlparse(db.DATABASE_URL)
    print(f"database: {parsed.hostname}/{parsed.path.lstrip('/')}")

    session = db.Session()
    try:
        rows = session.query(db.Document).filter(db.Document.content.is_(None)).order_by(db.Document.id).all()
        print(f"rows without content: {len(rows)}")
        for row in rows:
            content, reason = text_for(row.filename)
            if content is not None:
                row.content = content
                row.content_sha256 = content_hash(content)
                print(f"  filled   {row.filename} ({len(content)} characters)")
            elif row.retired:
                print(f"  skipped  {row.filename}: {reason} (already retired)")
            elif args.retire_unfillable:
                row.retired = True
                print(f"  retired  {row.filename}: {reason}")
            else:
                print(f"  can't fill {row.filename}: {reason} (still active: --retire-unfillable retires it)")
        if args.dry_run:
            session.rollback()
            print("dry run: nothing was changed")
        else:
            session.commit()
            print("committed")
    finally:
        session.close()


if __name__ == "__main__":
    main()
