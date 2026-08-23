"""Deterministic parsing: unit-level, one test per real structured sample file.

No Deps, no provider, no seam — `parse_structured` is a pure function of a `Document`,
which is the whole point of ADR-0009.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from invoice_automation.documents import load_document
from invoice_automation.structured_parsing import StructuredParseFailed, parse_structured


def test_json_parses_the_common_shape(invoices_dir: Path) -> None:
    invoice = parse_structured(load_document(invoices_dir / "invoice_1004.json"))

    assert invoice.invoice_number == "INV-1004"
    assert invoice.vendor.name == "Precision Parts Ltd."
    assert invoice.total == Decimal("1890.00")
    assert len(invoice.line_items) == 2
    assert invoice.line_items[0].item == "WidgetA"
    assert invoice.line_items[0].quantity == 3


def test_json_maps_amount_to_stated_amount(invoices_dir: Path) -> None:
    """invoice_1013.json's line items carry 'amount'; our schema calls it stated_amount."""
    invoice = parse_structured(load_document(invoices_dir / "invoice_1013.json"))

    assert len(invoice.line_items) == 8
    assert invoice.line_items[0].amount == Decimal("3750.00")
    # A repeated item at a different price is its own line, never merged.
    prices = {item.unit_price for item in invoice.line_items if item.item == "WidgetA"}
    assert prices == {Decimal("250.00"), Decimal("240.00")}


def test_json_preserves_an_empty_vendor_and_a_negative_total_unrepaired(
    invoices_dir: Path,
) -> None:
    """invoice_1009.json: parsing must not paper over what the document actually says."""
    invoice = parse_structured(load_document(invoices_dir / "invoice_1009.json"))

    assert invoice.vendor.name == ""
    assert invoice.due_date is None
    assert invoice.line_items[0].quantity == -5
    assert invoice.total == Decimal("-250.00")


def test_field_value_csv_preserves_both_repeated_line_items(invoices_dir: Path) -> None:
    """invoice_1006.csv: 'item'/'quantity'/'unit_price' each appear twice."""
    invoice = parse_structured(load_document(invoices_dir / "invoice_1006.csv"))

    assert invoice.vendor.name == "Acme Industrial Supplies"
    assert [item.item for item in invoice.line_items] == ["WidgetA", "WidgetB"]
    assert invoice.line_items[0].quantity == 5
    assert invoice.line_items[1].quantity == 3
    assert invoice.total == Decimal("2750.00")


def test_tabular_csv_excludes_trailing_summary_rows_from_line_items(
    invoices_dir: Path,
) -> None:
    """invoice_1007.csv: Subtotal/Tax/Total rows carry a blank Item column."""
    invoice = parse_structured(load_document(invoices_dir / "invoice_1007.csv"))

    assert len(invoice.line_items) == 3
    assert all(item.item not in ("Subtotal:", "Tax (6%):", "Total:") for item in invoice.line_items)
    assert invoice.subtotal == Decimal("14750.00")
    assert invoice.total == Decimal("15525.00")


def test_tabular_csv_dates_are_normalised_from_mm_dd_yyyy(invoices_dir: Path) -> None:
    from datetime import date

    invoice = parse_structured(load_document(invoices_dir / "invoice_1007.csv"))

    assert invoice.invoice_date == date(2026, 1, 28)
    assert invoice.due_date == date(2026, 2, 28)


def test_xml_parses_header_line_items_and_totals(invoices_dir: Path) -> None:
    invoice = parse_structured(load_document(invoices_dir / "invoice_1014.xml"))

    assert invoice.invoice_number == "INV-1014"
    assert invoice.vendor.name == "TechParts International"
    assert invoice.currency == "EUR"
    assert len(invoice.line_items) == 2
    assert invoice.line_items[0].item == "WidgetA"
    assert invoice.total == Decimal("4125.00")


def test_malformed_json_falls_back_rather_than_crashing(tmp_path: Path) -> None:
    path = tmp_path / "invoice_broken.json"
    path.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(StructuredParseFailed):
        parse_structured(load_document(path))


def test_json_missing_required_vendor_falls_back(tmp_path: Path) -> None:
    """No vendor at all is a shape violation Invoice can't validate — must fail cleanly,
    not raise something uncaught."""
    path = tmp_path / "invoice_novendor.json"
    path.write_text('{"line_items": []}', encoding="utf-8")

    with pytest.raises(StructuredParseFailed):
        parse_structured(load_document(path))
