"""Ticket 11: escalation interrupts the graph at approval (ADR-0005) and a human
decision resumes it. Every test shares one persistent-in-process checkpointer across
`run_invoice` and `resume_invoice`, since resumption reads state the run checkpointed —
there is nothing else for it to read from.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

from invoice_automation.deps import Deps
from invoice_automation.documents import load_document
from invoice_automation.graph import NotAwaitingReview, resume_invoice, run_invoice
from invoice_automation.models import HumanReview
from invoice_automation.providers import FakeProvider


def _escalating_deps(deps: Deps, tmp_path: Path, document_stem: str) -> Deps:
    """A document whose total alone crosses the $10K scrutiny line, clean on every
    other rule — isolates the escalation path the same way test_graph.py's own
    threshold fixture does."""
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir(exist_ok=True)
    (responses_dir / f"{document_stem}.json").write_text(
        '{"vendor": {"name": "Widgets Inc."}, "line_items": '
        '[{"item": "WidgetA", "quantity": 15, "unit_price": "250.00"}, '
        '{"item": "WidgetB", "quantity": 10, "unit_price": "500.00"}, '
        '{"item": "GadgetX", "quantity": 5, "unit_price": "750.00"}], '
        '"total": "12500.00"}',
        encoding="utf-8",
    )
    return Deps(
        provider=FakeProvider.with_sample_responses(responses_dir),
        catalogue=deps.catalogue,
        payment=deps.payment,
        clock=deps.clock,
        registry=deps.registry,
    )


def _document(tmp_path: Path, stem: str) -> Any:
    document_dir = tmp_path / "docs"
    document_dir.mkdir(exist_ok=True)
    path = document_dir / f"{stem}.txt"
    path.write_text("irrelevant to the fake", encoding="utf-8")
    return load_document(path)


@pytest.fixture
def checkpointer() -> Any:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    saver = SqliteSaver(conn)
    yield saver
    conn.close()


def test_an_escalated_invoice_interrupts_with_state_checkpointed_and_no_payment(
    tmp_path: Path, deps: Deps, checkpointer: Any
) -> None:
    document = _document(tmp_path, "invoice_escalated")
    scoped = _escalating_deps(deps, tmp_path, "invoice_escalated")

    result = run_invoice(document, scoped, checkpointer=checkpointer)

    assert result.decision is not None
    assert result.decision.outcome == "escalated"
    assert result.payment is None
    assert deps.payment.payments == []  # type: ignore[attr-defined]


def test_resuming_with_approval_pays_exactly_once(
    tmp_path: Path, deps: Deps, checkpointer: Any
) -> None:
    document = _document(tmp_path, "invoice_approve_me")
    scoped = _escalating_deps(deps, tmp_path, "invoice_approve_me")

    escalated = run_invoice(document, scoped, checkpointer=checkpointer)
    assert escalated.decision is not None and escalated.decision.outcome == "escalated"

    resumed = resume_invoice(
        document.name,
        HumanReview(outcome="approved", reason="Confirmed with the requester."),
        scoped,
        checkpointer=checkpointer,
    )

    assert resumed.decision is not None
    assert resumed.decision.outcome == "approved"
    assert resumed.human_review == HumanReview(
        outcome="approved", reason="Confirmed with the requester."
    )
    assert resumed.payment is not None
    assert resumed.payment.status == "success"
    assert len(scoped.payment.payments) == 1  # type: ignore[attr-defined]


def test_resuming_with_rejection_records_rejection_and_never_pays(
    tmp_path: Path, deps: Deps, checkpointer: Any
) -> None:
    document = _document(tmp_path, "invoice_reject_me")
    scoped = _escalating_deps(deps, tmp_path, "invoice_reject_me")

    run_invoice(document, scoped, checkpointer=checkpointer)

    resumed = resume_invoice(
        document.name,
        HumanReview(outcome="rejected", reason="Vendor could not be verified."),
        scoped,
        checkpointer=checkpointer,
    )

    assert resumed.decision is not None
    assert resumed.decision.outcome == "rejected"
    assert "Vendor could not be verified." in resumed.decision.reasoning
    assert resumed.human_review is not None and resumed.human_review.outcome == "rejected"
    assert resumed.payment is None
    assert scoped.payment.payments == []  # type: ignore[attr-defined]


def test_resuming_an_already_resumed_run_raises_and_does_not_pay_twice(
    tmp_path: Path, deps: Deps, checkpointer: Any
) -> None:
    document = _document(tmp_path, "invoice_double_resume")
    scoped = _escalating_deps(deps, tmp_path, "invoice_double_resume")

    run_invoice(document, scoped, checkpointer=checkpointer)
    review = HumanReview(outcome="approved", reason="Cleared by finance.")
    resume_invoice(document.name, review, scoped, checkpointer=checkpointer)

    with pytest.raises(NotAwaitingReview):
        resume_invoice(document.name, review, scoped, checkpointer=checkpointer)

    assert len(scoped.payment.payments) == 1  # type: ignore[attr-defined]


def test_resuming_a_thread_that_was_never_escalated_raises(
    tmp_path: Path, deps: Deps, checkpointer: Any
) -> None:
    document = load_document(Path("data/invoices/invoice_1001.txt"))
    approved = run_invoice(document, deps, checkpointer=checkpointer)
    assert approved.decision is not None and approved.decision.outcome == "approved"

    with pytest.raises(NotAwaitingReview):
        resume_invoice(
            document.name,
            HumanReview(outcome="approved", reason="n/a"),
            deps,
            checkpointer=checkpointer,
        )
