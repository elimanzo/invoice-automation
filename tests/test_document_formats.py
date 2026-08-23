"""One document of each format reaching a decision, through the primary seam.

Each test supplies a hand-written recorded response reflecting what a correct extraction
of that real document looks like, so the assertion is "the pipeline carried this format's
raw text through to a decision without mangling it" — not a claim about the model's own
judgement, which is what the evals in ticket 17 are for.
"""

from __future__ import annotations

import json
from pathlib import Path

from invoice_automation.deps import Deps
from invoice_automation.documents import load_document
from invoice_automation.graph import run_invoice
from invoice_automation.providers import FakeProvider


def _scoped_deps(deps: Deps, responses: dict[str, dict[str, object]], tmp_path: Path) -> Deps:
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir(exist_ok=True)
    for stem, payload in responses.items():
        (responses_dir / f"{stem}.json").write_text(json.dumps(payload), encoding="utf-8")
    return Deps(
        provider=FakeProvider.with_sample_responses(responses_dir),
        catalogue=deps.catalogue,
        payment=deps.payment,
        clock=deps.clock,
        registry=deps.registry,
    )


def test_plain_text_reaches_a_decision(invoices_dir: Path, deps: Deps, tmp_path: Path) -> None:
    document = load_document(invoices_dir / "invoice_1001.txt")
    scoped = _scoped_deps(
        deps,
        {
            "invoice_1001": {
                "vendor": {"name": "Widgets Inc."},
                "line_items": [{"item": "WidgetA", "quantity": 10, "unit_price": "250.00"}],
                "total": "5000.00",
            }
        },
        tmp_path,
    )

    result = run_invoice(document, scoped)

    assert result.decision is not None


def test_json_reaches_a_decision(invoices_dir: Path, deps: Deps, tmp_path: Path) -> None:
    document = load_document(invoices_dir / "invoice_1004.json")
    scoped = _scoped_deps(
        deps,
        {
            "invoice_1004": {
                "vendor": {"name": "Precision Parts Ltd."},
                "line_items": [{"item": "WidgetA", "quantity": 3, "unit_price": "250.00"}],
                "total": "1890.00",
            }
        },
        tmp_path,
    )

    result = run_invoice(document, scoped)

    assert result.decision is not None


def test_xml_reaches_a_decision(invoices_dir: Path, deps: Deps, tmp_path: Path) -> None:
    document = load_document(invoices_dir / "invoice_1014.xml")
    scoped = _scoped_deps(
        deps,
        {
            "invoice_1014": {
                "vendor": {"name": "TechParts International"},
                "line_items": [{"item": "WidgetA", "quantity": 4, "unit_price": "225.00"}],
                "total": "4125.00",
                "currency": "EUR",
            }
        },
        tmp_path,
    )

    result = run_invoice(document, scoped)

    assert result.decision is not None


def test_pdf_with_a_text_layer_reaches_a_decision(
    invoices_dir: Path, deps: Deps, tmp_path: Path
) -> None:
    document = load_document(invoices_dir / "invoice_1011.pdf")
    scoped = _scoped_deps(
        deps,
        {
            "invoice_1011": {
                "vendor": {"name": "Summit Manufacturing Co."},
                "line_items": [{"item": "WidgetA", "quantity": 6, "unit_price": "250.00"}],
                "total": "3000.00",
            }
        },
        tmp_path,
    )

    result = run_invoice(document, scoped)

    assert result.decision is not None


def test_forwarded_email_body_reaches_a_decision(
    invoices_dir: Path, deps: Deps, tmp_path: Path
) -> None:
    """INV-1008 arrives as an email, not an invoice document: headers, a greeting,
    prose describing the order, a sign-off. Nothing about that shape is special-cased
    anywhere — it is read as plain text like invoice_1001.txt."""
    document = load_document(invoices_dir / "invoice_1008.txt")
    scoped = _scoped_deps(
        deps,
        {
            "invoice_1008": {
                "vendor": {"name": "NoProd Industries"},
                "line_items": [
                    {"item": "SuperGizmo", "quantity": 12, "unit_price": "400.00"},
                    {"item": "MegaSprocket", "quantity": 6, "unit_price": "850.00"},
                ],
                "total": "9900.00",
            }
        },
        tmp_path,
    )

    result = run_invoice(document, scoped)

    assert result.decision is not None


def test_repeated_key_csv_preserves_every_line_item(
    invoices_dir: Path, deps: Deps, tmp_path: Path
) -> None:
    """invoice_1006.csv is a field,value CSV where 'item', 'quantity', and 'unit_price'
    each appear twice — once per line item. A naive dict built from that shape collapses
    to the last value per key, silently dropping the first line item. Nothing in
    load_document restructures the CSV at all: the whole file reaches extraction as one
    block of raw text, which is what this test guards."""
    document = load_document(invoices_dir / "invoice_1006.csv")
    # The repeated 'item' key survives raw, untouched by any premature dict-building —
    # that's the actual trap this test guards against.
    assert document.raw_text.count("item,Widget") == 2
    assert document.raw_text.count("quantity,") == 2

    scoped = _scoped_deps(
        deps,
        {
            "invoice_1006": {
                "vendor": {"name": "Acme Industrial Supplies"},
                "line_items": [
                    {"item": "WidgetA", "quantity": 5, "unit_price": "250.00"},
                    {"item": "WidgetB", "quantity": 3, "unit_price": "500.00"},
                ],
                "total": "2750.00",
            }
        },
        tmp_path,
    )

    result = run_invoice(document, scoped)

    assert result.invoice is not None
    assert len(result.invoice.line_items) == 2


def test_column_csv_trailing_totals_are_not_line_items(
    invoices_dir: Path, deps: Deps, tmp_path: Path
) -> None:
    """invoice_1007.csv has trailing rows (Subtotal:, Tax, Total:) with the item column
    blank. The raw text carries them through untouched; the recorded response here
    reflects a correct reading that excludes them from line_items, proving the plumbing
    doesn't force them in as fake items."""
    document = load_document(invoices_dir / "invoice_1007.csv")
    scoped = _scoped_deps(
        deps,
        {
            "invoice_1007": {
                "vendor": {"name": "MegaWidgets Corp"},
                "line_items": [
                    {"item": "WidgetA", "quantity": 20, "unit_price": "250.00"},
                    {"item": "WidgetB", "quantity": 15, "unit_price": "500.00"},
                    {"item": "GadgetX", "quantity": 3, "unit_price": "750.00"},
                ],
                "total": "15525.00",
            }
        },
        tmp_path,
    )

    result = run_invoice(document, scoped)

    assert result.invoice is not None
    assert len(result.invoice.line_items) == 3
    assert all(item.item != "Subtotal:" for item in result.invoice.line_items)
