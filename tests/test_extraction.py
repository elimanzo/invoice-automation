"""Extraction: a document becomes an invoice.

These assert external behaviour — the invoice that comes out — not how extraction
got there. Nothing here knows whether a provider was called or a parser ran.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from invoice_automation.deps import Deps
from invoice_automation.documents import load_document
from invoice_automation.extraction import extract_invoice


def test_clean_text_invoice_yields_both_line_items(invoices_dir: Path, deps: Deps) -> None:
    document = load_document(invoices_dir / "invoice_1001.txt")
    invoice = extract_invoice(document, deps)

    assert invoice.invoice_number == "INV-1001"
    assert invoice.vendor.name == "Widgets Inc."
    assert invoice.due_date == date(2026, 2, 1)
    assert invoice.total == Decimal("5000.00")

    assert [(item.item, item.quantity, item.unit_price) for item in invoice.line_items] == [
        ("WidgetA", 10, Decimal("250.00")),
        ("WidgetB", 5, Decimal("500.00")),
    ]


def test_line_item_amount_is_derived_when_absent(invoices_dir: Path, deps: Deps) -> None:
    document = load_document(invoices_dir / "invoice_1001.txt")
    invoice = extract_invoice(document, deps)

    # The document states no per-line amount; quantity times unit price is not a
    # correction, it is arithmetic.
    assert invoice.line_items[0].amount == Decimal("2500.00")


def test_line_items_are_never_merged(invoices_dir: Path, deps: Deps) -> None:
    """An item billed more than once stays more than one line item."""
    document = load_document(invoices_dir / "invoice_1013.json")
    invoice = extract_invoice(document, deps)

    widget_a_lines = [item for item in invoice.line_items if item.item == "WidgetA"]
    assert len(widget_a_lines) == 3
    assert {line.unit_price for line in widget_a_lines} == {
        Decimal("250.00"),
        Decimal("240.00"),
    }


def test_unknown_document_fails_loudly(tmp_path: Path, deps: Deps) -> None:
    """The fake provider has no canned answer here; that must not pass silently."""
    path = tmp_path / "invoice_9999.txt"
    path.write_text("INVOICE\nInvoice Number: INV-9999\n", encoding="utf-8")

    with pytest.raises(LookupError) as excinfo:
        extract_invoice(load_document(path), deps)

    assert "INV-9999" in str(excinfo.value)
