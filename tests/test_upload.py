import unicodedata

import pytest

import upload
from ingest import chunk_document
from pdf_samples import blank_pdf, encrypted_pdf, numbered_sections, pages_pdf, text_pdf
from retrieve import content_hash
from upload import (MAX_CHUNKS, MAX_FILENAME_LENGTH, MAX_PAGES, MAX_UPLOAD_BYTES, UploadRejected, check_fields,
                    check_filename, check_size, chunk_limit, drop_page_furniture, extract_text, page_furniture,
                    pdf_pages, prepare_upload)

FIELDS = {"owner": "Operations", "version": "2.0", "review_date": "2027-01-15"}


def rejected(check, *args, **kwargs) -> UploadRejected:
    """The UploadRejected that the check raises."""
    with pytest.raises(UploadRejected) as caught:
        check(*args, **kwargs)
    return caught.value


@pytest.mark.parametrize("name", [
    "Data Retention Policy v2.txt",
    "Datenschutzerklärung.pdf",
    "हिन्दी नीति.pdf",  # Hindi writes some vowels as combining marks
    "report (1).PDF",
    "Q&A, draft + notes.TXT",
    "Manager's notes.txt",
    "a_b-c.d.txt",
    "x" * (MAX_FILENAME_LENGTH - 4) + ".pdf",
])
def test_plain_names_in_any_language_are_accepted(name):
    assert check_filename(name) == name


@pytest.mark.parametrize("name", [
    "Manager’s notes.pdf",  # ’ right single quotation mark: the apostrophe Word and macOS type
    "‘Draft’ policy.txt",  # ‘ left single quotation mark
    "Policy – Final.pdf",  # – en dash
    "Retention—2027 review.txt",  # — em dash
])
def test_typographic_apostrophes_and_dashes_are_accepted(name):
    assert check_filename(name) == name


def test_a_name_is_nfc_normalised():
    decomposed = unicodedata.normalize("NFD", "Datenschutzerklärung.pdf")  # a + combining umlaut, as macOS may send it
    assert decomposed != "Datenschutzerklärung.pdf"
    assert check_filename(decomposed) == "Datenschutzerklärung.pdf"


@pytest.mark.parametrize("name, message", [
    ("", "no name"),
    (None, "no name"),
    ("../x.txt", "without a folder path"),
    ("a\\b.txt", "without a folder path"),
    (".env", "can't start with a dot"),
    ("x" * (MAX_FILENAME_LENGTH - 3) + ".pdf", "longer than 200"),
    ("a:b.txt", ":"),
    ("a#b.pdf", "#"),
    ("a?b.pdf", "?"),
    ("50%.pdf", "%"),
    ("a\tb.txt", "U+0009"),
    ("invoice‮fdp.exe.pdf", "U+202E"),  # right-to-left override: makes a name display differently
    ("a;b.txt", ";"),
    ("a.exe", "only .pdf and .txt"),
    ("a.pdf.exe", "only .pdf and .txt"),
    ("README", "only .pdf and .txt"),
])
def test_unsafe_or_unsupported_names_are_rejected_with_400(name, message):
    error = rejected(check_filename, name)
    assert error.status == 400
    assert message in error.message


def test_owner_version_and_date_are_trimmed():
    assert check_fields(" Operations ", "1.0 ", " 2027-01-15") == ("Operations", "1.0", "2027-01-15")


@pytest.mark.parametrize("owner, version, review_date, message", [
    ("  ", "1.0", "2027-01-15", "owner can't be empty"),
    ("Ops", "", "2027-01-15", "version can't be empty"),
    ("x" * 101, "1.0", "2027-01-15", "owner is longer than 100"),
    ("Ops", "x" * 51, "2027-01-15", "version is longer than 50"),
    ("Ops", "1.0", "20270115", "YYYY-MM-DD"),
    ("Ops", "1.0", "15/01/2027", "YYYY-MM-DD"),
    ("Ops", "1.0", "2027-02-30", "isn't a real date"),
])
def test_bad_fields_are_rejected_with_400(owner, version, review_date, message):
    error = rejected(check_fields, owner, version, review_date)
    assert error.status == 400
    assert message in error.message


def test_a_file_of_exactly_the_limit_passes_and_one_byte_more_is_413():
    check_size(b"a" * MAX_UPLOAD_BYTES)
    error = rejected(check_size, b"a" * (MAX_UPLOAD_BYTES + 1))
    assert error.status == 413
    assert "10 MB" in error.message


def test_an_empty_file_is_rejected_with_422():
    assert rejected(check_size, b"").status == 422


def test_a_text_file_loses_its_byte_order_mark_crlf_line_endings_and_nul_characters():
    data = "﻿Section 1. Scope\r\nWithin 72 hours.\x00\r\n".encode("utf-8")
    assert extract_text("policy.txt", data) == "Section 1. Scope\nWithin 72 hours."


def test_a_text_file_that_isnt_utf8_is_rejected_with_422():
    error = rejected(extract_text, "policy.txt", "café".encode("cp1252"))
    assert error.status == 422
    assert "UTF-8" in error.message


def test_a_text_file_of_only_whitespace_has_no_extractable_text():
    error = rejected(extract_text, "policy.txt", b"  \n\t\n")
    assert error.status == 422
    assert "no extractable text" in error.message


def test_a_pdf_with_a_text_layer_gives_its_text():
    data = text_pdf(["Section 1. Scope", "Within 72 hours."])
    assert extract_text("policy.PDF", data) == "Section 1. Scope\nWithin 72 hours."


def test_a_pdf_without_a_text_layer_is_rejected_with_422():
    error = rejected(extract_text, "scan.pdf", blank_pdf())
    assert error.status == 422
    assert "no text layer" in error.message


def test_a_file_that_isnt_really_a_pdf_is_rejected_with_422():
    error = rejected(extract_text, "report.pdf", b"%PDF-1.4 this is not really a PDF")
    assert error.status == 422
    assert "can't be read as a PDF" in error.message


def test_a_pdf_that_breaks_the_parser_outside_its_own_errors_is_422_and_logged(monkeypatch, caplog):
    def damaged(stream):
        raise KeyError("/Root")  # pypdf can raise this on a damaged file: not one of its own error classes

    monkeypatch.setattr(upload, "PdfReader", damaged)
    error = rejected(extract_text, "damaged.pdf", text_pdf(["Section 1. Scope"]))
    assert error.status == 422
    assert "can't be read as a PDF" in error.message
    assert any(record.name == "upload" and record.exc_info for record in caplog.records)  # the traceback is logged


def test_a_password_protected_pdf_is_rejected_with_422_and_says_so():
    error = rejected(extract_text, "locked.pdf", encrypted_pdf("secret"))
    assert error.status == 422
    assert "password-protected" in error.message


def test_a_pdf_locked_only_against_printing_or_copying_opens_without_a_password():
    # Encrypted with AES, which needs the cryptography package (it comes with google-auth): if that
    # package ever goes, this test fails rather than real uploads.
    data = encrypted_pdf("", owner_password="owner")
    assert extract_text("restricted.pdf", data) == "Section 1. Scope\nWithin 72 hours."


def test_the_page_limit_is_checked_before_any_text_is_read():
    # 300 blank pages pass the page check and are then refused for having no text; 301 are refused for their length.
    assert "no text layer" in rejected(extract_text, "scan.pdf", blank_pdf(MAX_PAGES)).message
    error = rejected(extract_text, "long.pdf", blank_pdf(MAX_PAGES + 1))
    assert error.status == 413
    assert "301 pages" in error.message
    assert "300-page limit" in error.message


def body(page: int) -> list[str]:
    """Four lines of text that appear on no other page."""
    letter = "ABCDEFGH"[page]
    return [f"Clause {letter} sets the scope.", f"Clause {letter} names the owner.",
            f"Clause {letter} lists the records.", f"Clause {letter} is reviewed yearly."]


def test_a_header_and_a_page_number_footer_on_every_page_are_dropped():
    pages = ["\n".join(["Example Corp - Privacy Policy", *body(n), f"Page {n} of 4"]) for n in range(1, 5)]
    assert page_furniture(pages) == {"Example Corp - Privacy Policy", "Page # of #"}
    assert drop_page_furniture(pages) == ["\n".join(body(n)) for n in range(1, 5)]


@pytest.mark.parametrize("pages_with_it, dropped", [(1, False), (2, True)])
def test_a_line_is_furniture_from_half_the_pages(pages_with_it, dropped):
    pages = ["\n".join(body(n)) for n in range(1, 5)]
    for n in range(pages_with_it):
        pages[n] = "DRAFT\n" + pages[n]
    assert ("DRAFT" in page_furniture(pages)) == dropped


def test_headers_that_alternate_between_odd_and_even_pages_are_dropped():
    # Like the Official Journal: one version on odd pages, another on even ones, and a page of each without
    # its header (footnotes pushed it off). Each version is on 3 of 8 pages: under half of all pages.
    pages = []
    for n in range(1, 9):
        header = [f"EN Official Journal {n}/8"] if n % 2 else [f"{n}/8 Official Journal EN"]
        pages.append("\n".join((header if n not in (7, 8) else []) + body(n - 1)))
    assert page_furniture(pages) == {"EN Official Journal #/#", "#/# Official Journal EN"}


def test_a_line_repeated_in_the_middle_of_every_page_is_kept():
    pages = ["\n".join([*body(n)[:2], "See the glossary for defined terms.", *body(n)[2:]]) for n in range(1, 5)]
    assert drop_page_furniture(pages) == pages


def test_a_pdf_of_one_or_two_pages_is_left_as_it_is():
    pages = ["\n".join(["Example Corp", *body(n), "Page 1"]) for n in range(1, 3)]
    assert drop_page_furniture(pages) == pages
    assert drop_page_furniture(pages[:1]) == pages[:1]


def test_article_headings_at_the_top_of_a_few_pages_are_kept():
    # "Article 5" and "Article 9" share the pattern "Article #", but only 2 of the 6 pages start with one.
    pages = ["\n".join(body(n)) for n in range(1, 7)]
    pages[1] = "Article 5\n" + pages[1]
    pages[4] = "Article 9\n" + pages[4]
    assert drop_page_furniture(pages) == pages


def test_a_pdf_without_repeated_lines_loses_none_of_its_text():
    data = pages_pdf([body(n) for n in range(1, 5)])
    assert page_furniture(pdf_pages(data)) == set()
    assert extract_text("policy.pdf", data) == "\n".join(line for n in range(1, 5) for line in body(n))


def test_a_sentence_split_by_a_page_break_is_joined_and_a_finished_one_is_not():
    data = pages_pdf([["The controller shall notify"], ["the authority without delay."], ["Records are kept."]])
    assert extract_text("policy.pdf", data) == "The controller shall notify the authority without delay.\nRecords are kept."


def test_a_word_hyphenated_across_a_page_break_is_rejoined():
    data = pages_pdf([["The controller shall noti-"], ["fy the supervisory authority."], ["Records are kept."]])
    assert extract_text("policy.pdf", data) == "The controller shall notify the supervisory authority.\nRecords are kept."


def test_a_sentence_split_by_a_page_footer_and_header_is_joined_once_they_are_gone():
    data = pages_pdf([
        ["Example Corp policy", "Breaches are reported within", "Page 1 of 3"],
        ["Example Corp policy", "seventy-two hours of discovery.", "Page 2 of 3"],
        ["Example Corp policy", "Records are kept for six years.", "Page 3 of 3"],
    ])
    assert extract_text("policy.pdf", data) == ("Breaches are reported within seventy-two hours of discovery.\n"
                                                "Records are kept for six years.")


def test_prepare_upload_returns_exactly_what_will_be_stored_and_indexed():
    upload = prepare_upload("Retention Policy.txt", b"Section 1. Scope\nKeep records for 6 years.", **FIELDS)
    assert (upload.filename, upload.owner, upload.version, upload.review_date) == (
        "Retention Policy.txt", "Operations", "2.0", "2027-01-15")
    assert upload.content == "Section 1. Scope\nKeep records for 6 years."
    assert upload.content_sha256 == content_hash(upload.content)
    assert upload.chunks == chunk_document(upload.content)


def test_a_text_of_exactly_the_chunk_limit_passes_and_one_chunk_more_is_413():
    assert len(prepare_upload("policy.txt", numbered_sections(MAX_CHUNKS), **FIELDS).chunks) == MAX_CHUNKS
    error = rejected(prepare_upload, "policy.txt", numbered_sections(MAX_CHUNKS + 1), **FIELDS)
    assert error.status == 413
    assert f"{MAX_CHUNKS + 1:,} chunks" in error.message
    assert f"{MAX_CHUNKS:,}-chunk limit" in error.message


@pytest.mark.parametrize("setting, limit", [(None, 300), ("", 300), ("2000", 2000), (" 150 ", 150)])
def test_the_chunk_limit_is_max_upload_chunks_or_300(setting, limit):
    assert chunk_limit(setting) == limit


@pytest.mark.parametrize("setting", ["abc", "0", "-5", "1.5", "2,000"])
def test_a_chunk_limit_that_isnt_a_positive_whole_number_stops_the_app(setting):
    with pytest.raises(ValueError, match="MAX_UPLOAD_CHUNKS"):
        chunk_limit(setting)


def test_the_cheapest_check_reports_first():
    # The name is bad, the file too big and not a PDF: the name is what gets reported.
    error = rejected(prepare_upload, "../x.pdf", b"a" * (MAX_UPLOAD_BYTES + 1), **FIELDS)
    assert error.status == 400
