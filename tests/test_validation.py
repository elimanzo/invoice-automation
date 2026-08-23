"""Validation rules, unit-level: an Invoice straight into validate_invoice, no seam."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from invoice_automation.deps import Deps
from invoice_automation.models import Invoice, LineItem, Vendor
from invoice_automation.validation import validate_invoice


def test_subtotal_matching_line_items_is_not_flagged(deps: Deps) -> None:
    invoice = Invoice(
        vendor=Vendor(name="X"),
        line_items=[LineItem(item="WidgetA", quantity=2, unit_price=Decimal("250.00"))],
        subtotal=Decimal("500.00"),
    )

    flags = validate_invoice(invoice, deps)

    assert not any(f.code == "total_mismatch" for f in flags)


def test_subtotal_disagreeing_with_line_items_is_flagged(deps: Deps) -> None:
    invoice = Invoice(
        vendor=Vendor(name="X"),
        line_items=[LineItem(item="WidgetA", quantity=2, unit_price=Decimal("250.00"))],
        subtotal=Decimal("999.00"),  # should be 500.00
    )

    flags = validate_invoice(invoice, deps)

    mismatch = [f for f in flags if f.code == "total_mismatch"]
    assert len(mismatch) == 1
    assert mismatch[0].severity.value == "soft"
    assert "999.00" in mismatch[0].message and "500.00" in mismatch[0].message


def test_a_taxed_total_disagreeing_with_line_items_is_not_a_false_positive(
    deps: Deps,
) -> None:
    """total = subtotal + tax is expected to differ from the line-item sum; only
    subtotal (pre-tax) should ever be compared against it."""
    invoice = Invoice(
        vendor=Vendor(name="X"),
        line_items=[LineItem(item="WidgetA", quantity=2, unit_price=Decimal("250.00"))],
        subtotal=Decimal("500.00"),
        tax_amount=Decimal("40.00"),
        total=Decimal("540.00"),
    )

    flags = validate_invoice(invoice, deps)

    assert not any(f.code == "total_mismatch" for f in flags)


def test_a_shipping_charge_not_modelled_does_not_produce_a_false_positive(deps: Deps) -> None:
    """invoice_1010.txt: subtotal + tax + an unmodelled $150 shipping charge = total.
    Comparing against subtotal (not total) is what keeps this from false-flagging."""
    invoice = Invoice(
        vendor=Vendor(name="X"),
        line_items=[LineItem(item="WidgetA", quantity=2, unit_price=Decimal("250.00"))],
        subtotal=Decimal("500.00"),
        tax_amount=Decimal("25.00"),
        total=Decimal("675.00"),  # 500 + 25 tax + 150 shipping, not modelled
    )

    flags = validate_invoice(invoice, deps)

    assert not any(f.code == "total_mismatch" for f in flags)


def test_an_unpriced_line_item_is_not_treated_as_a_mismatch(deps: Deps) -> None:
    """line_items_total is None when a line's amount can't be determined at all —
    that's a different, separate problem, not something this check should also flag."""
    invoice = Invoice(
        vendor=Vendor(name="X"),
        line_items=[LineItem(item="WidgetA", quantity=2)],  # no price stated anywhere
        subtotal=Decimal("500.00"),
    )

    flags = validate_invoice(invoice, deps)

    assert not any(f.code == "total_mismatch" for f in flags)


def test_invoice_1009_flags_the_real_data_integrity_problem(
    invoices_dir: Path, deps: Deps
) -> None:
    """The brief's own case: stated subtotal doesn't match the actual line items."""
    from invoice_automation.documents import load_document
    from invoice_automation.structured_parsing import parse_structured

    invoice = parse_structured(load_document(invoices_dir / "invoice_1009.json"))

    flags = validate_invoice(invoice, deps)

    assert any(f.code == "total_mismatch" for f in flags)
    assert any(f.code == "negative_quantity" for f in flags)
