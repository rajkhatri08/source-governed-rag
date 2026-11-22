import re
import json

# Each pattern matches the position just before a heading line, so every piece keeps its heading.
ARTICLE_HEADING = re.compile(r"^(?=Art\. \d+ GDPR$)", re.MULTILINE)  # a line that is exactly "Art. 12 GDPR"
SECTION_HEADING = re.compile(r"^(?=Section \d+\.)", re.MULTILINE)  # a line starting "Section 2."
NUMBERED_PARAGRAPH = re.compile(r"^(?=\d+\. )", re.MULTILINE)  # a line starting "3. " (not "3The": a sentence number)
BROKEN_WORD = re.compile(r"(?<=[a-z])-\n(?=[a-z])")  # "recommenda-\ntion": a word a PDF hyphenated at a line end

MAX_CHUNK_CHARS = 1500  # a longer article is split at its numbered paragraphs, any other longer piece is packed
MAX_TITLE_CHARS = 300  # the lines before the first heading start every piece only if they are this short: a title
PACK_CHARS = 1000  # with no headings, lines are packed into chunks of up to this size
OVERLAP_CHARS = 50  # when a line has to be cut, as in the 1000/50 run of August


def chunk_text(document, size, overlap):
    if overlap >= size:
        raise ValueError("overlap must be smaller than size")
    chunks = []
    for churn in range(0, len(document), size - overlap):
        chunks.append(document[churn:churn + size])
    return chunks


def chunk_by_article(document: str) -> list[str]:
    """Split before each `Art. N GDPR` heading line; each chunk starts with its heading."""
    parts = ARTICLE_HEADING.split(document)
    chunks = []
    for part in parts:
        stripped = part.strip()
        if stripped:
            chunks.append(stripped)
    return chunks


def chunk_document(text: str) -> list[str]:
    """Split cleaned text where the document's own structure splits: articles, else sections, else lines.

    No chunk is longer than MAX_CHUNK_CHARS: a longer piece is packed line by line. A heading match can
    be an accident (a cross-reference that happens to start a line), and must not make a giant chunk.
    """
    if ARTICLE_HEADING.search(text):
        pieces = chunk_articles(text)
    elif SECTION_HEADING.search(text):
        pieces = split_with_prefix(text, SECTION_HEADING)
    else:
        return pack_lines(text)
    return [chunk for piece in pieces for chunk in (pack_lines(piece) if len(piece) > MAX_CHUNK_CHARS else [piece])]


def chunk_articles(text: str) -> list[str]:
    """One chunk per article. An article over MAX_CHUNK_CHARS is split at its numbered paragraphs."""
    chunks = []
    for article in chunk_by_article(text):
        paragraphs = []
        if len(article) > MAX_CHUNK_CHARS:
            paragraphs = split_with_prefix(article, NUMBERED_PARAGRAPH)
        if paragraphs:
            chunks.extend(paragraphs)
        else:
            chunks.append(article)  # short, or no numbered paragraphs to split at
    return chunks


def split_with_prefix(text: str, heading: re.Pattern) -> list[str]:
    """Split before each heading line. The lines before the first heading (a title) start every piece.

    No heading in the text gives an empty list. Text before the first heading that is longer than a
    title (MAX_TITLE_CHARS) is content, not a title: it is packed into chunks of its own, and the pieces
    get no prefix.
    """
    parts = heading.split(text)
    prefix = " | ".join(line for line in parts[0].split("\n") if line)
    pieces = [part.strip() for part in parts[1:]]
    if not pieces:
        return []
    if len(prefix) > MAX_TITLE_CHARS:
        return pack_lines(parts[0].strip()) + pieces
    if prefix:
        return [f"{prefix} | {piece}" for piece in pieces]
    return pieces


def pack_lines(text: str) -> list[str]:
    """Pack lines into chunks of up to PACK_CHARS. A line over MAX_CHUNK_CHARS is cut with chunk_text.

    clean_text has already removed blank lines, so a line is the closest thing to a paragraph.
    """
    chunks = []
    current = ""
    for line in text.split("\n"):
        if len(line) > MAX_CHUNK_CHARS:
            if current:
                chunks.append(current)
            chunks.extend(chunk_text(line, PACK_CHARS, OVERLAP_CHARS))
            current = ""
        elif not current:
            current = line
        elif len(current) + 1 + len(line) <= PACK_CHARS:  # + 1 for the newline joining them
            current = current + "\n" + line
        else:
            chunks.append(current)
            current = line
    if current:
        chunks.append(current)
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


def join_broken_words(text: str) -> str:
    """Rejoin the words a PDF hyphenated at a line end: "recommenda-\\ntion" becomes "recommendation".

    Only a lower-case letter, a hyphen, a line break and a lower-case letter: "AI-\\nbased", "2019-\\n2020"
    and a dash after a space stay as they are. The cost: a compound broken at its own hyphen loses it
    ("pre-\\ntrain" becomes "pretrain").
    """
    return BROKEN_WORD.sub("", text)


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