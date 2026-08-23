"""Ticket 17: four invoices written by hand to test what the provided data does not
reach. They live in `data/invoices_extra/`, apart from the provided directory, so the
provided data stays pristine and what was added is obvious.

Each fixture is deterministically parsed JSON (per ADR-0009), except the image-only
PDF, which by construction has no text layer at all — so every scenario here is exact
by construction, with no provider needed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from invoice_automation.deps import Deps
from invoice_automation.documents import UnreadableDocument, load_document
from invoice_automation.graph import run_invoice

REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRA_INVOICES = REPO_ROOT / "data" / "invoices_extra"


def test_padded_price_is_caught_by_the_price_check_and_nothing_else(deps: Deps) -> None:
    """Known vendor, catalogued item, plenty of stock, well under the scrutiny
    threshold, but billed at $400 against a $250 catalogue expectation with no
    discount note. Nothing else about this invoice should raise a finding."""
    document = load_document(EXTRA_INVOICES / "invoice_9001_padded_price.json")

    result = run_invoice(document, deps)

    assert [f.code for f in result.flags] == ["price_above_expected"]
    assert result.decision is not None
    assert result.decision.outcome == "approved"


def test_threshold_pair_decides_differently_on_either_side_of_the_boundary(
    deps: Deps,
) -> None:
    """Two invoices, identical but for a total a dollar below and a dollar above the
    $10K scrutiny line, isolating the dollar-threshold rule from every other check."""
    under = run_invoice(
        load_document(EXTRA_INVOICES / "invoice_9002_threshold_under.json"), deps
    )
    over = run_invoice(
        load_document(EXTRA_INVOICES / "invoice_9003_threshold_over.json"), deps
    )

    assert under.decision is not None and under.decision.outcome == "approved"
    assert over.decision is not None and over.decision.outcome == "escalated"


def test_image_only_pdf_fails_with_a_clear_typed_error(deps: Deps) -> None:
    """No text layer at all — proving extraction fails gracefully, naming the file and
    the reason, rather than a stack trace or a silently empty invoice."""
    path = EXTRA_INVOICES / "invoice_9004_image_only.pdf"

    with pytest.raises(UnreadableDocument) as exc_info:
        load_document(path)

    assert path.name in str(exc_info.value)


def test_contradicting_duplicate_hard_flags_and_escalates_rather_than_paying(
    deps: Deps,
) -> None:
    """A second document for a known identity whose total disagrees with the first.
    The original pays cleanly; the contradiction is never merged or guessed at — it
    hard-flags heavily enough to force escalation on its own and never reaches payment."""
    original = run_invoice(
        load_document(EXTRA_INVOICES / "invoice_9005_a_original.json"), deps
    )
    assert original.decision is not None and original.decision.outcome == "approved"
    assert original.payment is not None and original.payment.status == "success"

    contradiction = run_invoice(
        load_document(EXTRA_INVOICES / "invoice_9005_b_contradiction.json"), deps
    )

    assert contradiction.decision is not None
    assert contradiction.decision.outcome == "escalated"
    assert contradiction.payment is None
    contradiction_flags = [
        f for f in contradiction.flags if f.code == "duplicate_contradiction"
    ]
    assert contradiction_flags
    message = contradiction_flags[0].message
    assert "250.00" in message and "999.00" in message
