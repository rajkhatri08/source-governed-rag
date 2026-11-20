"""Upload validation: every check runs before anything is stored, cheapest first.

prepare_upload runs the checks in order and returns what will be stored and indexed. Each check
raises UploadRejected with the HTTP status and the plain message the user sees. Nothing here
touches the database, the index or the disk.
"""
import io
import re
import unicodedata
from dataclasses import dataclass
from datetime import date

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from ingest import chunk_document, clean_pdf_text, clean_text
from retrieve import content_hash

MAX_UPLOAD_MB = 10
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
MAX_FILENAME_LENGTH = 200  # documents.filename is VARCHAR(200)
MAX_OWNER_LENGTH = 100  # documents.owner
MAX_VERSION_LENGTH = 50  # documents.version
EXTENSIONS = (".pdf", ".txt")
# Besides letters, digits and their combining marks, in any script. ’ ‘ – — are the apostrophes and dashes
# Word and macOS type. Not allowed, among others: ':' (it separates the parts of a chunk id), '#', '?' and '%'
# (they would break the document's URLs), '/' and '\'.
NAME_PUNCTUATION = set(" ._-()&,'+’‘–—")
REVIEW_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


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
    """The text of each page of a PDF, read in memory."""
    try:
        reader = PdfReader(io.BytesIO(data))
        return [page.extract_text() for page in reader.pages]
    except PyPdfError as e:
        raise UploadRejected(422, "this file can't be read as a PDF") from e


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
    text = clean_pdf_text("\n".join(pdf_pages(data))) if is_pdf else decode_text(data)
    content = clean_text(text.replace("\x00", ""))  # Postgres can't store the NUL character
    if not content:
        if is_pdf:
            raise UploadRejected(422, "no extractable text: this PDF has no text layer (is it a scan?)")
        raise UploadRejected(422, "no extractable text: the file holds only whitespace")
    return content


def prepare_upload(raw_name: str | None, data: bytes, *, owner: str, version: str,
                   review_date: str) -> PreparedUpload:
    """Run every check, cheapest first. Writes nothing; raises UploadRejected at the first failure."""
    filename = check_filename(raw_name)
    owner, version, review_date = check_fields(owner, version, review_date)
    check_size(data)
    content = extract_text(filename, data)
    return PreparedUpload(filename=filename, owner=owner, version=version, review_date=review_date,
                          content=content, content_sha256=content_hash(content), chunks=chunk_document(content))
