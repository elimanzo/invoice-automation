"""Ticket 10: reconciliation of duplicates and revisions.

Two documents for one invoice identity must not be treated as two invoices — the
pipeline pays the obligation once, however many documents describe it. Each test drives
the whole pipeline through `run_invoice`, the primary seam, rather than calling
`reconciliation.reconcile` directly, because the behaviour that matters is what actually
gets paid, not the intermediate flags.

A local `ScriptedProvider` is used instead of `FakeProvider.with_sample_responses`:
`FakeProvider` keys its recordings on a document's filename *stem*, so a `.pdf`/`.txt`
pair for the same invoice number collides on one recording. These tests need the two
documents in a pair to carry genuinely different extracted data, so responses are keyed
on the full document name instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from invoice_automation.catalogue import SqliteCatalogue
from invoice_automation.clock import FixedClock
from invoice_automation.deps import Deps
from invoice_automation.documents import load_document
from invoice_automation.graph import run_invoice
from invoice_automation.payments import RecordingPayment
from invoice_automation.providers import StructuredCall
from invoice_automation.reconciliation import RevisionAfterPayment
from invoice_automation.registry import SqliteRegistry

REPO_ROOT = Path(__file__).resolve().parents[1]
INVOICES = REPO_ROOT / "data" / "invoices"


@dataclass
class ScriptedProvider:
    """Replays one response per full document name (not stem), so a `.pdf`/`.txt`
    pair for the same invoice can carry deliberately different data."""

    responses: dict[str, dict[str, Any]] = field(default_factory=dict)

    def structured(self, call: StructuredCall) -> dict[str, Any]:
        if call.kind == "critique":
            return {"problem_found": False, "explanation": None}
        return self.responses[call.document_id]


@pytest.fixture
def scripted_deps(tmp_path: Path) -> Deps:
    return Deps(
        provider=ScriptedProvider(),
        catalogue=SqliteCatalogue(tmp_path / "catalogue.db"),
        payment=RecordingPayment(),
        clock=FixedClock(date(2026, 2, 1)),
        registry=SqliteRegistry(tmp_path / "registry.db"),
    )


def _clean_widget_a(quantity: int, unit_price: str, **overrides: Any) -> dict[str, Any]:
    """A response that approves cleanly: a known vendor, a known item, well under the
    scrutiny threshold — so the outcome under test is reconciliation, not approval."""
    payload: dict[str, Any] = {
        "invoice_number": "INV-1099",
        "vendor": {"name": "Widgets Inc."},
        "invoice_date": "2026-01-10",
        "due_date": "2026-02-10",
        "line_items": [{"item": "WidgetA", "quantity": quantity, "unit_price": unit_price}],
        "total": str(Decimal(unit_price) * quantity),
    }
    payload.update(overrides)
    return payload


def test_pdf_and_text_twin_pay_once_preferring_the_richer_source(scripted_deps: Deps) -> None:
    """INV-1011's real sample data: a PDF and a text twin, the text carrying fields the
    PDF's layout dropped. Per the ticket, the pair pays once, using the richer source."""
    provider = scripted_deps.provider
    assert isinstance(provider, ScriptedProvider)
    provider.responses["invoice_1011.pdf"] = _clean_widget_a(
        6, "250.00", invoice_number="INV-1011", subtotal=None, tax_amount=None, payment_terms=None
    )
    provider.responses["invoice_1011.txt"] = _clean_widget_a(
        6,
        "250.00",
        invoice_number="INV-1011",
        subtotal="1500.00",
        tax_amount="0.00",
        payment_terms="Net 30",
    )

    pdf_result = run_invoice(load_document(INVOICES / "invoice_1011.pdf"), scripted_deps)
    txt_result = run_invoice(load_document(INVOICES / "invoice_1011.txt"), scripted_deps)

    assert pdf_result.decision is not None and pdf_result.decision.outcome == "approved"
    assert txt_result.decision is not None and txt_result.decision.outcome == "approved"
    payment = scripted_deps.payment
    assert isinstance(payment, RecordingPayment)
    assert len(payment.payments) == 1
    assert txt_result.payment is not None and txt_result.payment.status == "skipped"
    assert any(f.code == "duplicate_enriched" for f in pdf_result.flags + txt_result.flags) or (
        txt_result.invoice is not None and txt_result.invoice.payment_terms == "Net 30"
    )
    assert txt_result.invoice is not None
    assert txt_result.invoice.subtotal == Decimal("1500.00")


def test_identical_copies_pay_once(scripted_deps: Deps) -> None:
    """INV-1012's real sample data: a PDF and a text twin that agree on everything.
    The second document is redundant, not a second obligation."""
    provider = scripted_deps.provider
    assert isinstance(provider, ScriptedProvider)
    identical = _clean_widget_a(2, "500.00", invoice_number="INV-1012")
    provider.responses["invoice_1012.pdf"] = dict(identical)
    provider.responses["invoice_1012.txt"] = dict(identical)

    first = run_invoice(load_document(INVOICES / "invoice_1012.pdf"), scripted_deps)
    second = run_invoice(load_document(INVOICES / "invoice_1012.txt"), scripted_deps)

    assert first.decision is not None and first.decision.outcome == "approved"
    assert second.decision is not None and second.decision.outcome == "approved"
    payment = scripted_deps.payment
    assert isinstance(payment, RecordingPayment)
    assert len(payment.payments) == 1
    assert second.payment is not None and second.payment.status == "skipped"
    assert any(f.code == "duplicate_document" for f in second.flags)


def test_revised_invoice_pays_its_revised_total_once(scripted_deps: Deps, tmp_path: Path) -> None:
    """A revision that arrives before the original was paid — e.g. the original was
    escalated for scrutiny rather than auto-approved — supersedes cleanly, and the
    obligation is paid once, at the revised total, not the sum of both versions.

    Both documents are JSON, parsed deterministically (ADR-0009); the provider is never
    called for either, so nothing needs scripting here — only real files on disk, since
    `load_document` requires a path that exists."""
    original_path = tmp_path / "invoice_5001.json"
    original_path.write_text(
        '{"invoice_number": "INV-5001", "vendor": {"name": "Widgets Inc."}, '
        '"line_items": [{"item": "WidgetA", "quantity": 15, "unit_price": 700.00}], '
        '"total": 10500.00}',
        encoding="utf-8",
    )
    revised_path = tmp_path / "invoice_5001_revised.json"
    revised_path.write_text(
        '{"invoice_number": "INV-5001", "revision": "R1", '
        '"vendor": {"name": "Widgets Inc."}, '
        '"line_items": [{"item": "WidgetA", "quantity": 4, "unit_price": 250.00}], '
        '"total": 1000.00}',
        encoding="utf-8",
    )

    original = run_invoice(load_document(original_path), scripted_deps)
    revised = run_invoice(load_document(revised_path), scripted_deps)

    assert original.decision is not None and original.decision.outcome == "escalated"
    assert original.payment is None
    assert revised.decision is not None and revised.decision.outcome == "approved"
    payment = scripted_deps.payment
    assert isinstance(payment, RecordingPayment)
    assert len(payment.payments) == 1
    _, amount, _ = payment.payments[0]
    assert amount == Decimal("1000.00")
    assert any(f.code == "revision_supersedes" for f in revised.flags)


def test_revision_after_payment_raises_for_a_human(scripted_deps: Deps) -> None:
    """The real sample data: `invoice_1004.json` (total 1890.00) auto-approves and pays
    before `invoice_1004_revised.json` (revision R1, total 5940.00) ever arrives. A
    revision changing the total after payment is not something code may resolve on its
    own — it must stop and say so, not silently pay the difference or ignore it."""
    original = run_invoice(load_document(INVOICES / "invoice_1004.json"), scripted_deps)
    assert original.decision is not None and original.decision.outcome == "approved"
    assert original.payment is not None and original.payment.status == "success"

    with pytest.raises(RevisionAfterPayment):
        run_invoice(load_document(INVOICES / "invoice_1004_revised.json"), scripted_deps)

    payment = scripted_deps.payment
    assert isinstance(payment, RecordingPayment)
    assert len(payment.payments) == 1  # the exception, not a second payment, is the outcome


def test_contradicting_copies_do_not_pay_and_both_values_appear(scripted_deps: Deps) -> None:
    """Two documents for the same identity disagreeing on total: reconciliation never
    guesses which is right, and refuses to let the invoice sail through — the
    contradiction alone must force escalation, and a human reviewing it needs both
    numbers, not just the latest one."""
    provider = scripted_deps.provider
    assert isinstance(provider, ScriptedProvider)
    provider.responses["invoice_1001.txt"] = _clean_widget_a(2, "100.00", invoice_number="INV-3001")

    first_doc = load_document(INVOICES / "invoice_1001.txt")
    first = run_invoice(first_doc, scripted_deps)
    assert first.decision is not None and first.decision.outcome == "approved"

    contradicting_payload = {
        "invoice_number": "INV-3001",
        "vendor": {"name": "Widgets Inc."},
        "line_items": [{"item": "WidgetA", "quantity": 2, "unit_price": "100.00"}],
        "total": "999.00",
    }
    provider.responses["invoice_1002.txt"] = contradicting_payload

    second = run_invoice(load_document(INVOICES / "invoice_1002.txt"), scripted_deps)

    payment = scripted_deps.payment
    assert isinstance(payment, RecordingPayment)
    assert len(payment.payments) == 1  # the first document's payment only
    assert second.decision is not None and second.decision.outcome != "approved"
    contradiction_flags = [f for f in second.flags if f.code == "duplicate_contradiction"]
    assert contradiction_flags
    message = contradiction_flags[0].message
    assert "200.00" in message and "999.00" in message


def test_a_document_silent_on_currency_does_not_false_contradict_an_explicit_one(
    scripted_deps: Deps,
) -> None:
    """`Invoice.currency` defaults to "USD" rather than None, so a document that never
    mentions currency looks — field for field — identical to one that explicitly (and
    correctly) states USD. A second document stating a genuinely different currency
    must still register as an enrichment (the first document simply never said), not a
    contradiction: nothing about the two documents actually disagrees."""
    provider = scripted_deps.provider
    assert isinstance(provider, ScriptedProvider)
    provider.responses["invoice_1001.txt"] = _clean_widget_a(2, "100.00", invoice_number="INV-4001")

    first = run_invoice(load_document(INVOICES / "invoice_1001.txt"), scripted_deps)
    assert first.decision is not None and first.decision.outcome == "approved"

    provider.responses["invoice_1002.txt"] = _clean_widget_a(
        2, "100.00", invoice_number="INV-4001", currency="EUR"
    )
    second = run_invoice(load_document(INVOICES / "invoice_1002.txt"), scripted_deps)

    assert not any(f.code == "duplicate_contradiction" for f in second.flags)
    assert second.invoice is not None and second.invoice.currency == "EUR"
