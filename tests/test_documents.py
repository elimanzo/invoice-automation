"""Format detection and text extraction — no LLM involved, no seam needed.

Behavioural, end-to-end format coverage lives in test_document_formats.py, through
run_invoice. This file is unit-level: does load_document read the right bytes, in the
right shape, for the right reason.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from invoice_automation.documents import (
    UndecodableDocument,
    UnreadableDocument,
    UnsupportedDocument,
    load_document,
)
from invoice_automation.models import DocumentFormat


def test_format_detected_from_content_even_with_the_wrong_suffix(tmp_path: Path) -> None:
    """A JSON invoice saved with a .txt extension must still be read as JSON."""
    path = tmp_path / "invoice_mislabelled.txt"
    path.write_text('{"vendor": {"name": "X"}}', encoding="utf-8")

    document = load_document(path)

    assert document.format is DocumentFormat.JSON


def test_xml_detected_from_content(tmp_path: Path) -> None:
    path = tmp_path / "invoice_odd.dat"
    path.write_text('<?xml version="1.0"?><invoice></invoice>', encoding="utf-8")
    # .dat has no suffix mapping at all — content sniffing is the only way this resolves.
    document = load_document(path)

    assert document.format is DocumentFormat.XML


def test_csv_and_text_fall_back_to_suffix_since_they_have_no_signature(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "invoice.csv"
    csv_path.write_text("a,b\n1,2\n", encoding="utf-8")
    txt_path = tmp_path / "invoice.txt"
    txt_path.write_text("plain text invoice body", encoding="utf-8")

    assert load_document(csv_path).format is DocumentFormat.CSV
    assert load_document(txt_path).format is DocumentFormat.TEXT


def test_an_unrecognised_extension_and_content_is_unsupported(tmp_path: Path) -> None:
    path = tmp_path / "invoice.docx"
    path.write_bytes(b"PK\x03\x04binary garbage, not one of our formats")

    with pytest.raises(UnsupportedDocument):
        load_document(path)


def test_a_pdf_with_a_text_layer_extracts_it(invoices_dir: Path) -> None:
    document = load_document(invoices_dir / "invoice_1011.pdf")

    assert document.format is DocumentFormat.PDF
    assert "INV-1011" in document.raw_text
    assert "Summit Manufacturing" in document.raw_text


def test_a_pdf_with_no_text_layer_is_refused(tmp_path: Path) -> None:
    """A blank single-page PDF: valid format, zero extractable text."""
    blank_pdf = (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
        b"trailer<</Size 4/Root 1 0 R>>\n"
        b"%%EOF\n"
    )
    path = tmp_path / "invoice_blank.pdf"
    path.write_bytes(blank_pdf)

    with pytest.raises(UnreadableDocument) as excinfo:
        load_document(path)

    assert "invoice_blank.pdf" in str(excinfo.value)


def test_an_undecodable_document_is_still_refused_not_mangled(tmp_path: Path) -> None:
    path = tmp_path / "invoice_bad.txt"
    path.write_bytes(b"Vendor: \xff\xfe\xfd invalid \x81\x8d\x8f")

    with pytest.raises(UndecodableDocument):
        load_document(path)
