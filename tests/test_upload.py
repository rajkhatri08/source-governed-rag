import unicodedata

import pytest

from ingest import chunk_document
from pdf_samples import blank_pdf, text_pdf
from retrieve import content_hash
from upload import (MAX_FILENAME_LENGTH, MAX_UPLOAD_BYTES, UploadRejected, check_fields, check_filename,
                    check_size, extract_text, prepare_upload)

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


def test_prepare_upload_returns_exactly_what_will_be_stored_and_indexed():
    upload = prepare_upload("Retention Policy.txt", b"Section 1. Scope\nKeep records for 6 years.", **FIELDS)
    assert (upload.filename, upload.owner, upload.version, upload.review_date) == (
        "Retention Policy.txt", "Operations", "2.0", "2027-01-15")
    assert upload.content == "Section 1. Scope\nKeep records for 6 years."
    assert upload.content_sha256 == content_hash(upload.content)
    assert upload.chunks == chunk_document(upload.content)


def test_the_cheapest_check_reports_first():
    # The name is bad, the file too big and not a PDF: the name is what gets reported.
    error = rejected(prepare_upload, "../x.pdf", b"a" * (MAX_UPLOAD_BYTES + 1), **FIELDS)
    assert error.status == 400
