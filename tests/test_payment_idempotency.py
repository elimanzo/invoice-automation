"""Payment is attempted at most once per invoice identity, through the primary seam.

Before ticket 09, nothing in `_pay` touched the registry at all — re-running the same
approved invoice would call the mock payment function again every time. This is the
regression test for that gap.
"""

from __future__ import annotations

from pathlib import Path

from invoice_automation.deps import Deps
from invoice_automation.documents import load_document


def test_reprocessing_the_same_invoice_pays_it_at_most_once(
    invoices_dir: Path, deps: Deps
) -> None:
    from invoice_automation.graph import run_invoice

    document = load_document(invoices_dir / "invoice_1001.txt")

    first = run_invoice(document, deps)
    second = run_invoice(document, deps)

    assert first.payment is not None and first.payment.status == "success"
    assert second.payment is not None and second.payment.status == "skipped"
    assert len(deps.payment.payments) == 1  # type: ignore[attr-defined]


def test_a_registry_that_already_holds_the_payment_is_respected_from_the_start(
    tmp_path: Path, deps: Deps
) -> None:
    """Not just reprocessing the same in-process run — a registry pre-populated from a
    prior process entirely must also prevent a fresh run from paying again."""
    from decimal import Decimal

    from invoice_automation.graph import run_invoice
    from invoice_automation.registry import SqliteRegistry

    registry_path = tmp_path / "registry.db"
    registry = SqliteRegistry(registry_path)
    registry.record_payment("1001", "Widgets Inc.", Decimal("5000.00"))

    scoped = Deps(
        provider=deps.provider,
        catalogue=deps.catalogue,
        payment=deps.payment,
        clock=deps.clock,
        registry=registry,
    )
    document = load_document(Path("data/invoices/invoice_1001.txt"))

    result = run_invoice(document, scoped)

    assert result.payment is not None
    assert result.payment.status == "skipped"
    assert deps.payment.payments == []  # type: ignore[attr-defined]


def test_an_escalated_or_rejected_invoice_is_never_recorded_as_paid(
    invoices_dir: Path, deps: Deps
) -> None:
    """Only an approved-and-actually-paid invoice should ever occupy a registry slot —
    an escalated one must remain payable once a human clears it (ticket 11)."""
    from invoice_automation.graph import run_invoice

    document = load_document(invoices_dir / "invoice_1013.json")  # rejected, stock aggregation

    result = run_invoice(document, deps)

    assert result.decision is not None and result.decision.outcome == "rejected"
    assert deps.registry.payment_recorded("1013") is False
