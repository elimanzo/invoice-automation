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

    flags = validate_invoice(invoice, deps).flags

    assert not any(f.code == "total_mismatch" for f in flags)


def test_subtotal_disagreeing_with_line_items_is_flagged(deps: Deps) -> None:
    invoice = Invoice(
        vendor=Vendor(name="X"),
        line_items=[LineItem(item="WidgetA", quantity=2, unit_price=Decimal("250.00"))],
        subtotal=Decimal("999.00"),  # should be 500.00
    )

    flags = validate_invoice(invoice, deps).flags

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

    flags = validate_invoice(invoice, deps).flags

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

    flags = validate_invoice(invoice, deps).flags

    assert not any(f.code == "total_mismatch" for f in flags)


def test_an_unpriced_line_item_is_not_treated_as_a_mismatch(deps: Deps) -> None:
    """line_items_total is None when a line's amount can't be determined at all —
    that's a different, separate problem, not something this check should also flag."""
    invoice = Invoice(
        vendor=Vendor(name="X"),
        line_items=[LineItem(item="WidgetA", quantity=2)],  # no price stated anywhere
        subtotal=Decimal("500.00"),
    )

    flags = validate_invoice(invoice, deps).flags

    assert not any(f.code == "total_mismatch" for f in flags)


def test_invoice_1009_flags_the_real_data_integrity_problem(
    invoices_dir: Path, deps: Deps
) -> None:
    """The brief's own case: stated subtotal doesn't match the actual line items."""
    from invoice_automation.documents import load_document
    from invoice_automation.structured_parsing import parse_structured

    invoice = parse_structured(load_document(invoices_dir / "invoice_1009.json"))

    flags = validate_invoice(invoice, deps).flags

    assert any(f.code == "total_mismatch" for f in flags)
    assert any(f.code == "negative_quantity" for f in flags)


# ---------------------------------------------------------------------------
# Item matching (ADR-0007): normalise, then match exactly. Fuzzy only suggests.
# ---------------------------------------------------------------------------


def test_spacing_and_casing_variants_still_match_the_catalogue(deps: Deps) -> None:
    """'Widget A' (a space) must resolve to the catalogue's 'WidgetA' — no stock flag."""
    invoice = Invoice(
        vendor=Vendor(name="X"),
        line_items=[LineItem(item="Widget A", quantity=2, unit_price=Decimal("250.00"))],
    )

    flags = validate_invoice(invoice, deps).flags

    assert not any(f.code in ("unknown_item", "stock_exceeded") for f in flags)


def test_an_uncatalogued_item_stays_unknown_even_when_it_resembles_a_real_one(
    deps: Deps,
) -> None:
    """WidgetC scores 0.857 similarity against WidgetA (see ADR-0007) — close enough
    that a naive fuzzy threshold would wrongly match it. It must not."""
    invoice = Invoice(
        vendor=Vendor(name="X"),
        line_items=[LineItem(item="WidgetC", quantity=3, unit_price=Decimal("350.00"))],
    )

    flags = validate_invoice(invoice, deps).flags

    unknown = [f for f in flags if f.code == "unknown_item"]
    assert len(unknown) == 1
    assert "WidgetC" in unknown[0].message
    assert not any(f.code == "stock_exceeded" for f in flags)


def test_invoice_1016_widgetc_is_never_matched_to_a_similarly_named_catalogue_entry(
    invoices_dir: Path, deps: Deps
) -> None:
    from invoice_automation.documents import load_document
    from invoice_automation.structured_parsing import parse_structured

    invoice = parse_structured(load_document(invoices_dir / "invoice_1016.json"))

    flags = validate_invoice(invoice, deps).flags

    assert any(f.code == "unknown_item" and "WidgetC" in f.message for f in flags)
    assert not any(f.code == "stock_exceeded" for f in flags)


# ---------------------------------------------------------------------------
# Stock aggregation
# ---------------------------------------------------------------------------


def test_quantities_aggregate_across_line_items_before_the_stock_check(deps: Deps) -> None:
    """Two lines of 10 each, neither alone over the 15-unit stock, but 20 together is."""
    invoice = Invoice(
        vendor=Vendor(name="X"),
        line_items=[
            LineItem(item="WidgetA", quantity=10, unit_price=Decimal("250.00")),
            LineItem(item="WidgetA", quantity=10, unit_price=Decimal("250.00")),
        ],
    )

    flags = validate_invoice(invoice, deps).flags

    exceeded = [f for f in flags if f.code == "stock_exceeded"]
    assert len(exceeded) == 1  # one flag for the item, not one per line
    assert "20" in exceeded[0].message


def test_a_negative_quantity_line_is_excluded_from_aggregation(deps: Deps) -> None:
    """A corrupted negative line must not cancel out a legitimate positive one."""
    invoice = Invoice(
        vendor=Vendor(name="X"),
        line_items=[
            LineItem(item="WidgetA", quantity=20, unit_price=Decimal("250.00")),
            LineItem(item="WidgetA", quantity=-20, unit_price=Decimal("250.00")),
        ],
    )

    flags = validate_invoice(invoice, deps).flags

    assert any(f.code == "negative_quantity" for f in flags)
    # 20 alone still exceeds the 15-unit stock; it must not net to 0 and pass silently.
    assert any(f.code == "stock_exceeded" for f in flags)


def test_zero_stock_item_is_its_own_distinct_finding(deps: Deps) -> None:
    invoice = Invoice(
        vendor=Vendor(name="X"),
        line_items=[LineItem(item="FakeItem", quantity=100, unit_price=Decimal("1000.00"))],
    )

    flags = validate_invoice(invoice, deps).flags

    zero_stock = [f for f in flags if f.code == "zero_stock_item"]
    assert len(zero_stock) == 1
    assert zero_stock[0].severity.value == "fatal"
    assert not any(f.code == "stock_exceeded" for f in flags)


# ---------------------------------------------------------------------------
# Vendor
# ---------------------------------------------------------------------------


def test_a_known_vendor_is_not_flagged(deps: Deps) -> None:
    invoice = Invoice(vendor=Vendor(name="Widgets Inc."), line_items=[])

    flags = validate_invoice(invoice, deps).flags

    assert not any(f.code in ("unknown_vendor", "empty_vendor") for f in flags)


def test_an_unfamiliar_vendor_is_a_soft_flag(deps: Deps) -> None:
    invoice = Invoice(vendor=Vendor(name="Fraudster LLC"), line_items=[])

    flags = validate_invoice(invoice, deps).flags

    unknown = [f for f in flags if f.code == "unknown_vendor"]
    assert len(unknown) == 1
    assert unknown[0].severity.value == "soft"


def test_an_empty_vendor_name_is_flagged_distinctly_from_unknown(deps: Deps) -> None:
    invoice = Invoice(vendor=Vendor(name=""), line_items=[])

    flags = validate_invoice(invoice, deps).flags

    assert any(f.code == "empty_vendor" for f in flags)
    assert not any(f.code == "unknown_vendor" for f in flags)


# ---------------------------------------------------------------------------
# Price
# ---------------------------------------------------------------------------


def test_a_price_at_or_below_expected_is_not_flagged(deps: Deps) -> None:
    invoice = Invoice(
        vendor=Vendor(name="X"),
        line_items=[LineItem(item="WidgetA", quantity=1, unit_price=Decimal("250.00"))],
    )

    flags = validate_invoice(invoice, deps).flags

    assert not any(f.code.startswith("price_above_expected") for f in flags)


def test_an_undocumented_price_premium_is_the_heavier_flag(deps: Deps) -> None:
    invoice = Invoice(
        vendor=Vendor(name="X"),
        line_items=[LineItem(item="WidgetA", quantity=1, unit_price=Decimal("400.00"))],
    )

    flags = validate_invoice(invoice, deps).flags

    assert any(f.code == "price_above_expected" for f in flags)
    assert not any(f.code == "price_above_expected_documented" for f in flags)


def test_a_documented_price_premium_carries_the_lighter_code(deps: Deps) -> None:
    """INV-1010's WidgetA rush order: a note explaining the premium changes the flag,
    and RISK_WEIGHTS gives it less weight (config.py)."""
    invoice = Invoice(
        vendor=Vendor(name="X"),
        line_items=[
            LineItem(
                item="WidgetA", quantity=1, unit_price=Decimal("300.00"), note="rush order"
            )
        ],
    )

    flags = validate_invoice(invoice, deps).flags

    assert any(f.code == "price_above_expected_documented" for f in flags)
    assert not any(f.code == "price_above_expected" for f in flags)


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


def test_due_date_before_invoice_date_is_flagged(deps: Deps) -> None:
    from datetime import date

    invoice = Invoice(
        vendor=Vendor(name="X"),
        line_items=[],
        invoice_date=date(2026, 1, 30),
        due_date=date(2026, 1, 29),
    )

    flags = validate_invoice(invoice, deps).flags

    assert any(f.code == "due_date_before_invoice_date" for f in flags)


def test_due_date_in_the_past_is_flagged_against_the_injected_clock(deps: Deps) -> None:
    from datetime import date

    # conftest's FixedClock is set to 2026-02-01.
    invoice = Invoice(vendor=Vendor(name="X"), line_items=[], due_date=date(2026, 1, 15))

    flags = validate_invoice(invoice, deps).flags

    assert any(f.code == "due_date_in_the_past" for f in flags)


def test_a_future_due_date_is_not_flagged(deps: Deps) -> None:
    from datetime import date

    invoice = Invoice(vendor=Vendor(name="X"), line_items=[], due_date=date(2026, 6, 1))

    flags = validate_invoice(invoice, deps).flags

    assert not any(f.code == "due_date_in_the_past" for f in flags)


# ---------------------------------------------------------------------------
# Currency
# ---------------------------------------------------------------------------


def test_a_usd_invoice_is_not_flagged_and_needs_no_conversion(deps: Deps) -> None:
    invoice = Invoice(vendor=Vendor(name="X"), line_items=[], total=Decimal("100.00"))

    result = validate_invoice(invoice, deps)

    assert not any(f.code == "non_usd_currency" for f in result.flags)
    assert result.corrections == []
    assert result.usd_total == Decimal("100.00")


def test_a_non_usd_invoice_is_converted_and_flagged_with_an_audited_correction(
    deps: Deps,
) -> None:
    """INV-1014: EUR 4,125.00 at 1.08 USD/EUR = 4,455.00 USD."""
    invoice = Invoice(
        vendor=Vendor(name="X"), line_items=[], total=Decimal("4125.00"), currency="EUR"
    )

    result = validate_invoice(invoice, deps)

    assert result.usd_total == Decimal("4455.00")
    assert any(f.code == "non_usd_currency" and f.severity.value == "soft" for f in result.flags)
    assert len(result.corrections) == 1
    correction = result.corrections[0]
    assert correction.raw == "4125.00 EUR"
    assert correction.value == "4455.00 USD"
    assert "1.08" in correction.reason


def test_an_unconfigured_currency_raises_rather_than_silently_comparing_wrong_units(
    deps: Deps,
) -> None:
    import pytest

    from invoice_automation.config import UnknownCurrency

    invoice = Invoice(
        vendor=Vendor(name="X"), line_items=[], total=Decimal("100.00"), currency="XYZ"
    )

    with pytest.raises(UnknownCurrency):
        validate_invoice(invoice, deps)


# ---------------------------------------------------------------------------
# Purchase order reference
# ---------------------------------------------------------------------------


def test_a_cited_purchase_order_is_flagged_as_uncheckable(deps: Deps) -> None:
    invoice = Invoice(
        vendor=Vendor(name="X"), line_items=[], purchase_order_reference="PO-20260115"
    )

    flags = validate_invoice(invoice, deps).flags

    uncheckable = [f for f in flags if f.code == "po_reference_uncheckable"]
    assert len(uncheckable) == 1
    assert uncheckable[0].severity.value == "info"
    assert "PO-20260115" in uncheckable[0].message


def test_no_po_reference_means_no_flag(deps: Deps) -> None:
    invoice = Invoice(vendor=Vendor(name="X"), line_items=[])

    flags = validate_invoice(invoice, deps).flags

    assert not any(f.code == "po_reference_uncheckable" for f in flags)


# ---------------------------------------------------------------------------
# Risk score
# ---------------------------------------------------------------------------


def test_risk_score_sums_only_soft_flag_weights(deps: Deps) -> None:
    from invoice_automation.approval import compute_risk_score

    invoice = Invoice(
        vendor=Vendor(name="Fraudster LLC"),  # unknown_vendor, soft
        line_items=[
            LineItem(item="WidgetC", quantity=1, unit_price=Decimal("1.00"))
        ],  # unknown_item, soft
    )

    flags = validate_invoice(invoice, deps).flags

    score = compute_risk_score(flags)

    assert score == 6  # unknown_vendor(3) + unknown_item(3)


def test_a_single_moderate_soft_flag_does_not_cross_the_escalation_threshold(
    deps: Deps,
) -> None:
    from invoice_automation.approval import compute_risk_score
    from invoice_automation.config import RISK_ESCALATION_THRESHOLD

    invoice = Invoice(vendor=Vendor(name="Fraudster LLC"), line_items=[])

    flags = validate_invoice(invoice, deps).flags

    assert compute_risk_score(flags) < RISK_ESCALATION_THRESHOLD


# ---------------------------------------------------------------------------
# Price checking must compare in one currency (found by code-review on PR #3)
# ---------------------------------------------------------------------------


def test_a_foreign_price_below_the_usd_catalogue_number_but_above_it_once_converted_is_flagged(
    deps: Deps,
) -> None:
    """invoice_1014.xml: WidgetB billed at 475.00 EUR. 475 <= 500 (the catalogue's USD
    price) looks fine read naively, but 475 EUR converts to 513 USD at the configured
    1.08 rate — genuinely above catalogue. Comparing the raw numbers across currencies
    let a real overpriced line item through undetected."""
    invoice = Invoice(
        vendor=Vendor(name="X"),
        currency="EUR",
        line_items=[LineItem(item="WidgetB", quantity=1, unit_price=Decimal("475.00"))],
    )

    flags = validate_invoice(invoice, deps).flags

    priced = [f for f in flags if f.code == "price_above_expected"]
    assert len(priced) == 1
    assert "513.00 USD" in priced[0].message


def test_a_foreign_price_genuinely_under_the_converted_catalogue_price_is_not_flagged(
    deps: Deps,
) -> None:
    invoice = Invoice(
        vendor=Vendor(name="X"),
        currency="EUR",
        line_items=[LineItem(item="WidgetB", quantity=1, unit_price=Decimal("400.00"))],
    )

    flags = validate_invoice(invoice, deps).flags

    assert not any(f.code.startswith("price_above_expected") for f in flags)


def test_an_unconfigured_currency_in_a_line_price_still_raises_cleanly(deps: Deps) -> None:
    import pytest

    from invoice_automation.config import UnknownCurrency

    invoice = Invoice(
        vendor=Vendor(name="X"),
        currency="XYZ",
        line_items=[LineItem(item="WidgetA", quantity=1, unit_price=Decimal("999.00"))],
    )

    with pytest.raises(UnknownCurrency):
        validate_invoice(invoice, deps)
