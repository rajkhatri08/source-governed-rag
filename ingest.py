import re
import json
from pypdf import PdfReader


def chunk_text(document, size, overlap):
    if overlap >= size:
        raise ValueError("overlap must be smaller than size")
    chunks = []
    for churn in range(0, len(document), size - overlap):
        chunks.append(document[churn:churn + size])
    return chunks


def chunk_by_article(document):
    parts = re.split(r"(?=Art\. \d+ GDPR)", document)
    chunks = []
    for part in parts:
        stripped = part.strip()
        if stripped:
            chunks.append(stripped)
    return chunks


def clean_text(document):
    document = re.sub(r"\[\d+\]", "", document)
    lines = document.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if stripped:
            cleaned.append(stripped)
    return "\n".join(cleaned)


def clean_pdf_text(document):
    document = document.replace("ﬁ", "fi").replace("ﬂ", "fl")
    document = re.sub(r"(?<![.\n])\n(?![A-Z0-9(])", " ", document)
    document = re.sub(r" +", " ", document)
    return document


def load_document(path):
    with open(path, "r") as f:
        return f.read()


def load_metadata(path):
    with open(path, "r") as f:
        return json.load(f)


def load_pdf(path):
    reader = PdfReader(path)
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text())
    return "\n".join(pages)