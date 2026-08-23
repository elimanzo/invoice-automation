"""The central design claim behind the risk score: an invoice that passes every
individual rule can still be caught by several of them compounding.

INV-1008 is the case this exists for — $9,900, $100 under the dollar threshold, an
unknown vendor, and two items nowhere in the catalogue. No single rule stops it. The
accumulated risk does.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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


def test_invoice_1008_escalates_on_compounding_risk_despite_passing_every_rule(
    invoices_dir: Path, deps: Deps
) -> None:
    document = load_document(invoices_dir / "invoice_1008.txt")
    provider = _ScriptedProvider(
        {
            "invoice_number": "INV-1008",
            "vendor": {"name": "NoProd Industries"},
            "invoice_date": "2026-01-10",
            "due_date": "2026-01-20",
            "line_items": [
                {"item": "SuperGizmo", "quantity": 12, "unit_price": "400.00"},
                {"item": "MegaSprocket", "quantity": 6, "unit_price": "850.00"},
            ],
            "total": "9900.00",
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
    assert result.invoice.total == 9900  # under the $10K threshold on its own

    # The rules it passes individually:
    assert not any(f.severity.value == "fatal" for f in result.flags)  # nothing rejects it
    codes = {f.code for f in result.flags}
    assert "unknown_vendor" in codes
    assert sum(1 for f in result.flags if f.code == "unknown_item") == 2

    # What catches it anyway:
    assert result.decision is not None
    assert result.decision.outcome == "escalated"
    assert "Risk score" in result.decision.reasoning
    assert result.payment is None
