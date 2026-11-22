"""Upload validation: every check runs before anything is stored, cheapest first.

prepare_upload runs the checks in order and returns what will be stored and indexed. Each check
raises UploadRejected with the HTTP status and the plain message the user sees. Nothing here
touches the database, the index or the disk.
"""
import io
import logging
import os
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import date

from dotenv import load_dotenv
from pypdf import PasswordType, PdfReader

from ingest import chunk_document, clean_pdf_text, clean_text, join_broken_words
from retrieve import content_hash

load_dotenv()
logger = logging.getLogger(__name__)

MAX_UPLOAD_MB = 10
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
MAX_PAGES = 300
# Embedding is the slow part of an upload: about 0.22 CPU-seconds per chunk on a laptop (P0.3b), and Render's
# free instance has a tenth of a CPU. 300 chunks is about 300,000 characters of text.
DEFAULT_MAX_CHUNKS = 300
MAX_FILENAME_LENGTH = 200  # documents.filename is VARCHAR(200)
MAX_OWNER_LENGTH = 100  # documents.owner
MAX_VERSION_LENGTH = 50  # documents.version
EXTENSIONS = (".pdf", ".txt")
# Besides letters, digits and their combining marks, in any script. ’ ‘ – — are the apostrophes and dashes
# Word and macOS type. Not allowed, among others: ':' (it separates the parts of a chunk id), '#', '?' and '%'
# (they would break the document's URLs), '/' and '\'.
NAME_PUNCTUATION = set(" ._-()&,'+’‘–—")
REVIEW_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
# Page furniture: headers, footers and page numbers repeated on most pages of a PDF.
FURNITURE_EDGE_LINES = 2  # only a page's first 2 and last 2 non-blank lines can be furniture
FURNITURE_MIN_PAGES = 3  # on a shorter PDF, "repeated on most pages" means nothing
DIGITS = re.compile(r"\d+")


def chunk_limit(setting: str | None) -> int:
    """The chunk limit: MAX_UPLOAD_CHUNKS if it is set, else the default. A bad value stops the app at startup."""
    if not setting:
        return DEFAULT_MAX_CHUNKS
    if not setting.strip().isdigit() or int(setting) < 1:
        raise ValueError(f"MAX_UPLOAD_CHUNKS must be a whole number, at least 1: {setting!r} isn't")
    return int(setting)


MAX_CHUNKS = chunk_limit(os.getenv("MAX_UPLOAD_CHUNKS"))


class UploadRejected(Exception):
    """An upload refused before anything was stored: the HTTP status and the message the user sees."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class PreparedUpload:
    """An upload that passed every check: exactly what gets stored and indexed."""
    filename: str
    owner: str
    version: str
    review_date: str
    content: str  # the extracted, cleaned text: stored in documents.content, then chunked
    content_sha256: str
    chunks: list[str]


def allowed_in_name(character: str) -> bool:
    """A letter, digit or combining mark in any script (Hindi writes vowels as marks), or the listed punctuation."""
    return unicodedata.category(character)[0] in ("L", "N", "M") or character in NAME_PUNCTUATION


def describe(characters: list[str]) -> str:
    """Characters for an error message: visible ones as they are, invisible ones by their code point."""
    return " ".join(c if c.isprintable() and not c.isspace() else f"U+{ord(c):04X}" for c in characters)


def check_filename(raw: str | None) -> str:
    """The upload's name, NFC-normalised, if it is a plain file name this system can use safely."""
    name = unicodedata.normalize("NFC", raw or "")  # "é" typed as e + accent becomes the single character é
    if not name:
        raise UploadRejected(400, "the file has no name")
    if "/" in name or "\\" in name:
        raise UploadRejected(400, "send the file name only, without a folder path")
    if name.startswith("."):
        raise UploadRejected(400, "a file name can't start with a dot")
    if len(name) > MAX_FILENAME_LENGTH:
        raise UploadRejected(400, f"the file name is longer than {MAX_FILENAME_LENGTH} characters")
    not_allowed = sorted({c for c in name if not allowed_in_name(c)})
    if not_allowed:
        raise UploadRejected(400, f"the file name contains characters that aren't allowed: {describe(not_allowed)}. "
                                  "Use letters, digits, spaces and . _ - ( ) & , ' + ’ ‘ – —")
    if not name.lower().endswith(EXTENSIONS):
        raise UploadRejected(400, "only .pdf and .txt files can be uploaded")
    return name


def check_fields(owner: str, version: str, review_date: str) -> tuple[str, str, str]:
    """Owner, version and review date, trimmed, if they fit their columns and the date is real."""
    owner, version, review_date = owner.strip(), version.strip(), review_date.strip()
    for field, value, limit in (("owner", owner, MAX_OWNER_LENGTH), ("version", version, MAX_VERSION_LENGTH)):
        if not value:
            raise UploadRejected(400, f"{field} can't be empty")
        if len(value) > limit:
            raise UploadRejected(400, f"{field} is longer than {limit} characters")
    if not REVIEW_DATE.fullmatch(review_date):
        raise UploadRejected(400, "review_date must be YYYY-MM-DD")
    try:
        date.fromisoformat(review_date)
    except ValueError:
        raise UploadRejected(400, f"review_date {review_date} isn't a real date")
    return owner, version, review_date


def check_size(data: bytes) -> None:
    """Refuse an empty file, or one over the limit. The caller reads at most MAX_UPLOAD_BYTES + 1 bytes."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadRejected(413, f"the file is larger than {MAX_UPLOAD_MB} MB, the upload limit")
    if not data:
        raise UploadRejected(422, "the file is empty")


def pdf_pages(data: bytes) -> list[str]:
    """The text of each page of a PDF, read in memory. A locked or over-long PDF is refused before any text is read."""
    try:
        reader = PdfReader(io.BytesIO(data))
        # pypdf has already tried the empty password, which opens a PDF locked only against printing or copying.
        if reader.is_encrypted and reader.decrypt("") == PasswordType.NOT_DECRYPTED:
            raise UploadRejected(422, "this PDF is password-protected: remove the password and upload it again")
        pages = len(reader.pages)
        if pages > MAX_PAGES:
            raise UploadRejected(413, f"the PDF has {pages} pages, more than the {MAX_PAGES}-page limit: "
                                      "split it into smaller files")
        return [page.extract_text() for page in reader.pages]
    except UploadRejected:
        raise  # the refusals above pass through unchanged
    except Exception as e:
        # Real-world PDFs break pypdf in more ways than its own PyPdfError: a KeyError on a damaged file,
        # DependencyError on an unsupported encryption. The file is at fault, not the server, so it's a 422.
        logger.exception("pypdf couldn't read a PDF")
        raise UploadRejected(422, "this file can't be read as a PDF: it may be damaged, "
                                  "or use a feature this system doesn't support") from e


def line_pattern(line: str) -> str:
    """A line with each run of digits replaced by #, so "Page 3 of 10" and "Page 4 of 10" match."""
    return DIGITS.sub("#", line.strip())


def edge_positions(lines: list[str]) -> set[int]:
    """Where a page's first and last FURNITURE_EDGE_LINES non-blank lines are."""
    filled = [i for i, line in enumerate(lines) if line.strip()]
    return set(filled[:FURNITURE_EDGE_LINES] + filled[-FURNITURE_EDGE_LINES:])


def edge_patterns_on_half(pages: list[str]) -> set[str]:
    """The line patterns at the top or bottom of at least half of these pages, and of at least two of them."""
    pages_with = Counter()
    for page in pages:
        lines = page.split("\n")
        pages_with.update({line_pattern(lines[i]) for i in edge_positions(lines)})  # a set: once per page
    return {pattern for pattern, count in pages_with.items() if count >= 2 and 2 * count >= len(pages)}


def page_furniture(pages: list[str]) -> set[str]:
    """Headers, footers and page numbers: line patterns at the top or bottom of at least half the pages.

    Odd and even pages are also counted on their own: printed documents often alternate their headers
    and footers between left- and right-hand pages, so each version is on only half of all pages. (The
    EU's Official Journal does, and its footnotes push the footer off the last line of some pages.)
    """
    if len(pages) < FURNITURE_MIN_PAGES:
        return set()
    return edge_patterns_on_half(pages) | edge_patterns_on_half(pages[0::2]) | edge_patterns_on_half(pages[1::2])


def drop_page_furniture(pages: list[str]) -> list[str]:
    """Each page without its furniture. Only a page's edge lines can go; everything else is left as it was."""
    furniture = page_furniture(pages)
    kept = []
    for page in pages:
        lines = page.split("\n")
        dropped = {i for i in edge_positions(lines) if line_pattern(lines[i]) in furniture}
        kept.append("\n".join(line for i, line in enumerate(lines) if i not in dropped))
    return kept


def decode_text(data: bytes) -> str:
    """A .txt file's text: UTF-8, without the byte-order mark Notepad adds, with \\n line endings."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise UploadRejected(422, "this .txt file isn't UTF-8 text: save it as UTF-8 and upload it again") from e
    return text.replace("\r\n", "\n").replace("\r", "\n")  # the line endings reading the file as text gives


def extract_text(filename: str, data: bytes) -> str:
    """The cleaned text of a .pdf or .txt file: what documents.content stores. Backfill uses it too."""
    is_pdf = filename.lower().endswith(".pdf")
    if is_pdf:
        # Furniture goes before the pages are joined, while each page's first and last lines are still known.
        # Pages are joined by a single line break, so clean_pdf_text joins a sentence that runs onto the next
        # page exactly as it joins one that runs onto the next line. (pypdf ends each page with a line break of
        # its own: left in, it made every page break a paragraph break, splitting sentences.) Hyphenated words
        # are rejoined before clean_pdf_text turns their line breaks into spaces.
        pages = [page.strip() for page in drop_page_furniture(pdf_pages(data))]
        text = clean_pdf_text(join_broken_words("\n".join(pages)))
    else:
        text = decode_text(data)
    content = clean_text(text.replace("\x00", ""))  # Postgres can't store the NUL character
    if not content:
        if is_pdf:
            raise UploadRejected(422, "no extractable text: this PDF has no text layer (is it a scan?)")
        raise UploadRejected(422, "no extractable text: the file holds only whitespace")
    return content


def check_chunk_count(chunks: list[str]) -> None:
    """Refuse a text too long to embed and hold in memory on this server."""
    if len(chunks) > MAX_CHUNKS:
        raise UploadRejected(413, f"the text is too long to index: it splits into {len(chunks):,} chunks, "
                                  f"more than the {MAX_CHUNKS:,}-chunk limit: split it into smaller files")


def prepare_upload(raw_name: str | None, data: bytes, *, owner: str, version: str,
                   review_date: str) -> PreparedUpload:
    """Run every check, cheapest first. Writes nothing; raises UploadRejected at the first failure."""
    filename = check_filename(raw_name)
    owner, version, review_date = check_fields(owner, version, review_date)
    check_size(data)
    content = extract_text(filename, data)
    chunks = chunk_document(content)
    check_chunk_count(chunks)
    return PreparedUpload(filename=filename, owner=owner, version=version, review_date=review_date,
                          content=content, content_sha256=content_hash(content), chunks=chunks)
