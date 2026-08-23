"""Corrections and repair through the primary seam and the CLI.

Live-verified against real Grok on invoice_1002.txt, invoice_1003.txt, invoice_1010.txt,
and invoice_1012.txt this session; these tests reproduce the same shapes offline with a
scripted provider standing in for what the real model returned, so the suite stays
hermetic while covering the same ground.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from invoice_automation.cli import render
from invoice_automation.deps import Deps
from invoice_automation.documents import load_document
from invoice_automation.graph import run_invoice
from invoice_automation.providers import StructuredCall


class _ScriptedProvider:
    def __init__(self, extraction_payload: dict[str, Any]) -> None:
        self._extraction_payload = extraction_payload
        self.calls: list[StructuredCall] = []

    def structured(self, call: StructuredCall) -> dict[str, Any]:
        self.calls.append(call)
        if call.kind == "critique":
            return {"problem_found": False, "explanation": None}
        return self._extraction_payload


def test_the_ocr_corrupted_invoice_processes_with_its_corrections_recorded(
    invoices_dir: Path, deps: Deps
) -> None:
    """invoice_1012.txt: OCR-corrupted date and amount, real vendor rename, a PO
    reference in free text — the repair pass runs regardless of what the model returns."""
    document = load_document(invoices_dir / "invoice_1012.txt")
    provider = _ScriptedProvider(
        {
            "invoice_number": "INV 1012",
            "vendor": {"name": "QuickShip Distributers (formerly FastShip Ltd.)"},
            "invoice_date": "2026-01-26",
            "due_date": "2026-02-25",
            "line_items": [
                {"item": "Widget A", "quantity": 12, "unit_price": "250.00"},
                {"item": "WidgetB", "quantity": 7, "unit_price": "500.00"},
                {"item": "Gadget X", "quantity": 4, "unit_price": "750.00"},
            ],
            "subtotal": "9500.00",
            "tax_amount": "475.00",
            "total": "9975.00",
            "payment_terms": "Net 30",
        }
    )
    scoped = Deps(
        provider=provider,
        catalogue=deps.catalogue,
        payment=deps.payment,
        clock=deps.clock,
        registry=deps.registry,
    )

    result = run_invoice(document, scoped)

    assert result.invoice is not None
    assert result.invoice.total == Decimal("9975.00")
    assert len(result.corrections) == 2
    reasons = {c.field for c in result.corrections}
    assert reasons == {"raw_text:date", "raw_text:amount"}
    # The model saw the repaired text, not the corrupted original.
    assert "26-Jan-2026" in provider.calls[0].user
    assert "2O26" not in provider.calls[0].user


def test_misspelled_labels_extract_vendor_items_and_total_correctly(
    invoices_dir: Path, deps: Deps
) -> None:
    """invoice_1002.txt: Vndr/Itms/Inv #/Amt abbreviations. This mirrors exactly what
    real Grok returned live this session."""
    document = load_document(invoices_dir / "invoice_1002.txt")
    provider = _ScriptedProvider(
        {
            "invoice_number": "1002",
            "vendor": {"name": "Gadgets Co."},
            "invoice_date": "2026-01-30",
            "due_date": "2026-01-30",
            "line_items": [{"item": "GadgetX", "quantity": 20, "unit_price": "750.00"}],
            "total": "15000.00",
            "payment_terms": "Net 30",
        }
    )
    scoped = Deps(
        provider=provider,
        catalogue=deps.catalogue,
        payment=deps.payment,
        clock=deps.clock,
        registry=deps.registry,
    )

    result = run_invoice(document, scoped)

    assert result.invoice is not None
    assert result.invoice.vendor.name == "Gadgets Co."
    assert result.invoice.total == Decimal("15000.00")
    assert len(result.invoice.line_items) == 1
    assert result.invoice.line_items[0].item == "GadgetX"
    assert result.invoice.line_items[0].quantity == 20


def test_cli_renders_corrections_and_flags(invoices_dir: Path, deps: Deps) -> None:
    """This ticket's whole point is auditability — the CLI must actually show what it
    changed, not just track it internally."""
    document = load_document(invoices_dir / "invoice_1012.txt")
    provider = _ScriptedProvider(
        {
            "vendor": {"name": "QuickShip Distributers"},
            "line_items": [{"item": "WidgetA", "quantity": 12, "unit_price": "250.00"}],
            "total": "3000.00",
        }
    )
    scoped = Deps(
        provider=provider,
        catalogue=deps.catalogue,
        payment=deps.payment,
        clock=deps.clock,
        registry=deps.registry,
    )

    result = run_invoice(document, scoped)
    output = render(result, document_name=document.name)

    assert "Corrections:" in output
    assert "26-Jan-2O26" in output
    assert "26-Jan-2026" in output
    assert "confidence 0.95" in output
