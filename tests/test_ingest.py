from pathlib import Path

import pytest

from ingest import (MAX_CHUNK_CHARS, chunk_document, chunk_text, clean_pdf_text, clean_text, join_broken_words,
                    load_document)

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


@pytest.mark.parametrize("text, joined", [
    ("a recommenda-\ntion", "a recommendation"),
    ("Rad-\nford et al.", "Radford et al."),
    ("Re-\nsponsibility", "Responsibility"),
])
def test_a_word_hyphenated_at_a_line_end_is_rejoined(text, joined):
    assert join_broken_words(text) == joined


@pytest.mark.parametrize("text", [
    "AI-\nbased systems",  # a capital before the hyphen: a compound such as "AI-based"
    "the Pre-\nTrial stage",  # a capital after it
    "from 2019-\n2020",  # digits
    "costs -\nwhich rise",  # a dash after a space
    "well-known terms",  # a hyphen that isn't at a line end
])
def test_other_hyphens_at_line_ends_are_left_alone(text):
    assert join_broken_words(text) == text


def long_text(lines: int) -> str:
    """Lines of ordinary sentences with no heading: about 90 characters each."""
    return "\n".join(f"Line {n} of a long regulation sets out a rule that providers must follow." for n in range(lines))


def test_a_section_reference_that_starts_a_line_doesnt_make_the_document_one_chunk():
    # A long text whose only "Section N." line is a cross-reference that happened to wrap to a line start
    text = long_text(60) + "\nSection 2. of Annex V sets out the declaration.\n" + long_text(40)
    chunks = chunk_document(text)
    assert len(chunks) > 5
    assert max(len(chunk) for chunk in chunks) <= MAX_CHUNK_CHARS
    assert not any(chunk.startswith("Line 0") and "Section 2." in chunk for chunk in chunks)  # no giant title prefix


def test_a_section_longer_than_the_cap_is_packed_line_by_line():
    text = "Retention Policy\nSection 1. Scope\n" + long_text(40) + "\nSection 2. Review\nReviewed yearly."
    chunks = chunk_document(text)
    assert max(len(chunk) for chunk in chunks) <= MAX_CHUNK_CHARS
    assert chunks[0].startswith("Retention Policy | Section 1. Scope")
    assert chunks[-1] == "Retention Policy | Section 2. Review\nReviewed yearly."


@pytest.mark.parametrize("text, cleaned", [
    ("ﬁle", "file"),
    ("ﬂow", "flow"),
    ("aﬀect", "affect"),  # as gdpr1.pdf reads before this fix
    ("eﬃcient", "efficient"),
    ("baﬄe", "baffle"),
])
def test_a_ligature_in_a_pdf_becomes_its_letters(text, cleaned):
    assert clean_pdf_text(text) == cleaned


def test_a_line_break_inside_a_sentence_becomes_a_space():
    assert clean_pdf_text("reported within\nseventy-two hours") == "reported within seventy-two hours"


@pytest.mark.parametrize("text", [
    "Breaches are reported.\nRecords are kept.",  # after a full stop
    "Scope\nThe policy applies to staff.",  # before a capital: a heading ended, or a sentence starts
    "the following rights\n2. Access",  # before a digit: a numbered paragraph
    "the following rights\n(a) access",  # before a bracket: a lettered point
])
def test_a_line_break_that_can_end_a_line_on_purpose_is_kept(text):
    assert clean_pdf_text(text) == text


def test_runs_of_spaces_become_one():
    assert clean_pdf_text("within   72  hours") == "within 72 hours"
