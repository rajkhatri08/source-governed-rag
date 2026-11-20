from pathlib import Path

import pytest

from ingest import chunk_document, chunk_text, clean_text, load_document

DOCUMENTS = Path(__file__).parent.parent / "documents"
FILLER = " ".join(["The controller shall keep a record of this."] * 20)  # 899 characters


def cleaned(name: str) -> str:
    """A seed document's text as indexing sees it."""
    return clean_text(load_document(DOCUMENTS / name))


def test_a_short_article_stays_one_chunk_unchanged():
    text = "Art. 1 GDPR\nTest article\n1. First paragraph.\n2. Second paragraph."
    assert chunk_document(text) == [text]


def test_a_long_article_splits_at_its_numbered_paragraphs_each_with_heading_and_title():
    text = f"Art. 1 GDPR\nTest article\n1. First. {FILLER}\n2. Second. {FILLER}"
    assert chunk_document(text) == [
        f"Art. 1 GDPR | Test article | 1. First. {FILLER}",
        f"Art. 1 GDPR | Test article | 2. Second. {FILLER}",
    ]


def test_only_a_numbered_paragraph_line_starts_a_new_chunk():
    paragraph_2 = [
        "2. 1Where requests are excessive, the controller may either:",
        "(a) charge a fee; or",
        "(b) refuse to act.",
        f"3The controller shall bear the burden. {FILLER}",
        "13 and 14 may be provided in combination.",
        "Suitable Recitals",
        "(58) The Principle of Transparency",
    ]
    text = "\n".join(["Art. 1 GDPR", "Test article", f"1. {FILLER}"] + paragraph_2)
    chunks = chunk_document(text)
    assert len(chunks) == 2
    assert chunks[1] == "Art. 1 GDPR | Test article | " + "\n".join(paragraph_2)


def test_a_line_citing_an_article_is_not_a_heading():
    text = "Breach Handling Note\nArt. 33 GDPR requires notification within 72 hours.\nWe notify the DPO first."
    assert chunk_document(text) == [text]


def test_each_section_carries_the_document_title_lines():
    text = "Test Policy\nVersion 2.0\nSection 1. Scope\nApplies to staff.\nSection 2. Deadlines\nReport within 5 days."
    assert chunk_document(text) == [
        "Test Policy | Version 2.0 | Section 1. Scope\nApplies to staff.",
        "Test Policy | Version 2.0 | Section 2. Deadlines\nReport within 5 days.",
    ]


def test_sections_with_no_title_lines_get_no_prefix():
    text = "Section 1. Scope\nApplies to staff.\nSection 2. Deadlines\nReport within 5 days."
    assert chunk_document(text) == ["Section 1. Scope\nApplies to staff.", "Section 2. Deadlines\nReport within 5 days."]


def test_without_headings_lines_are_packed_up_to_1000_characters_in_order():
    lines = [f"Line {i}. " + " ".join(["word"] * 60) for i in range(10)]  # 308 characters each
    text = "\n".join(lines)
    chunks = chunk_document(text)
    assert len(chunks) == 4  # three lines fit in 1,000 characters, a fourth would not
    assert all(len(chunk) <= 1000 for chunk in chunks)
    assert "\n".join(chunks) == text  # nothing lost, nothing reordered


def test_a_line_over_1500_characters_is_cut_with_overlap():
    long_line = "abcdefghij" * 200
    chunks = chunk_document(f"Intro line.\n{long_line}\nClosing line.")
    assert chunks == ["Intro line."] + chunk_text(long_line, 1000, 50) + ["Closing line."]
    assert chunks[1][-50:] == chunks[2][:50]


def test_empty_text_gives_no_chunks():
    assert chunk_document("") == []


def test_the_gdpr_seed_file_gives_one_chunk_per_paragraph_each_naming_its_article():
    chunks = chunk_document(cleaned("gdpr_ch3.txt"))
    headings = [chunk.split(" | ")[0] for chunk in chunks]
    assert headings == ["Art. 12 GDPR"] * 8 + ["Art. 15 GDPR"] * 4 + ["Art. 17 GDPR"] * 3 + ["Art. 33 GDPR"] * 5


SEED_POLICIES = [
    ("retention_policy_v1.txt", "Data Retention Policy | Version 1.0", 3),
    ("draft_ai_policy.txt", "Draft AI Usage Policy | Version 0.3 - DRAFT, NOT APPROVED", 3),
    ("security_policy_v1.txt", "Information Security Policy | Version 1.0", 1),
    ("vendor_policy_v1.txt", "Vendor Management Policy | Version 1.0", 1),
]


@pytest.mark.parametrize("name, title, count", SEED_POLICIES)
def test_each_seed_policy_is_chunked_by_section_under_its_title(name, title, count):
    chunks = chunk_document(cleaned(name))
    assert len(chunks) == count
    for number, chunk in enumerate(chunks, start=1):
        assert chunk.startswith(f"{title} | Section {number}.")
        assert "GDPR" not in chunk  # no internal policy poses as a GDPR article
