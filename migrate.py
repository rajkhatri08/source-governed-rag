from db import Session, Document
from ingest import load_metadata

metadata = load_metadata("documents/metadata.json")

session = Session()

for filename in metadata:
    m = metadata[filename]
    existing = session.query(Document).filter(Document.filename == filename).first()
    if existing:
        print("skipping, already exists:", filename)
        continue
    doc = Document(
        filename=filename,
        owner=m["owner"],
        version=m["version"],
        approved=m["approved"],
        review_date=m["review_date"]
    )
    session.add(doc)
    print("added:", filename)

session.commit()
session.close()