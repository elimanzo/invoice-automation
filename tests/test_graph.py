"""The graph tracer bullet: one document, all four stages, one seam.

Asserted at `run_invoice` — the primary seam — never at the individual node functions,
so the graph's internals can be rebuilt without touching these tests.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from invoice_automation.deps import Deps
from invoice_automation.documents import load_document
from invoice_automation.graph import run_invoice


def test_clean_invoice_is_approved_and_paid_once(invoices_dir: Path, deps: Deps) -> None:
    document = load_document(invoices_dir / "invoice_1001.txt")

    result = run_invoice(document, deps)

    assert result.decision is not None
    assert result.decision.outcome == "approved"
    assert result.payment is not None
    assert result.payment.status == "success"

    assert deps.payment.payments == [("Widgets Inc.", Decimal("5000.00"), "USD")]  # type: ignore[attr-defined]


def test_multi_line_invoice_aggregates_past_stock_and_is_rejected(
    invoices_dir: Path, deps: Deps
) -> None:
    """INV-1013 bills WidgetA across three lines (15+5+2=22, against 15 in stock),
    WidgetB across two (10+8=18, against 10), and GadgetX across three (5+3+1=9,
    against 5). No single line exceeds stock; the aggregate does for all three — the
    check ticket 07 exists for. That fatal finding rejects outright, ahead of the
    $22,562.80 total's own trip over the $10K scrutiny line."""
    document = load_document(invoices_dir / "invoice_1013.json")

    result = run_invoice(document, deps)

    assert result.decision is not None
    assert result.decision.outcome == "rejected"
    stock_flags = {f.code for f in result.flags if f.code == "stock_exceeded"}
    assert stock_flags == {"stock_exceeded"}
    assert sum(1 for f in result.flags if f.code == "stock_exceeded") == 3
    assert result.payment is None
    assert deps.payment.payments == []  # type: ignore[attr-defined]


def test_an_invoice_over_threshold_with_no_stock_issue_is_escalated_not_rejected(
    tmp_path: Path, deps: Deps
) -> None:
    """The dollar-threshold path, isolated from stock aggregation: every real sample
    invoice over $10K also happens to trip a stock or catalogue rule, so this scenario
    needs an authored fixture to test on its own. Every quantity here sits exactly at
    its stock limit, not over it — clean on catalogue grounds, over on price alone."""
    from invoice_automation.providers import FakeProvider

    document_dir = tmp_path / "docs"
    document_dir.mkdir()
    (document_dir / "invoice_bigclean.txt").write_text("irrelevant to the fake", "utf-8")

    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    (responses_dir / "invoice_bigclean.json").write_text(
        '{"vendor": {"name": "Widgets Inc."}, "line_items": '
        '[{"item": "WidgetA", "quantity": 15, "unit_price": "250.00"}, '
        '{"item": "WidgetB", "quantity": 10, "unit_price": "500.00"}, '
        '{"item": "GadgetX", "quantity": 5, "unit_price": "750.00"}], '
        '"total": "12500.00"}',
        encoding="utf-8",
    )
    scoped_deps = Deps(
        provider=FakeProvider.with_sample_responses(responses_dir),
        catalogue=deps.catalogue,
        payment=deps.payment,
        clock=deps.clock,
        registry=deps.registry,
    )
    document = load_document(document_dir / "invoice_bigclean.txt")

    result = run_invoice(document, scoped_deps)

    assert not any(f.severity.value == "fatal" for f in result.flags)
    assert result.decision is not None
    assert result.decision.outcome == "escalated"
    assert result.payment is None


def test_a_line_item_exceeding_stock_is_flagged(tmp_path: Path, deps: Deps) -> None:
    """Validation checks stock per line item; this must show up as a flag."""
    from invoice_automation.providers import FakeProvider

    document_dir = tmp_path / "docs"
    document_dir.mkdir()
    (document_dir / "invoice_overstock.txt").write_text("irrelevant to the fake", "utf-8")

    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    (responses_dir / "invoice_overstock.json").write_text(
        '{"vendor": {"name": "Widgets Inc."}, "line_items": '
        '[{"item": "WidgetA", "quantity": 999, "unit_price": "250.00"}], '
        '"total": "249750.00"}',
        encoding="utf-8",
    )
    fake = FakeProvider.with_sample_responses(responses_dir)
    scoped_deps = Deps(
        provider=fake,
        catalogue=deps.catalogue,
        payment=deps.payment,
        clock=deps.clock,
        registry=deps.registry,
    )

    document = load_document(document_dir / "invoice_overstock.txt")
    result = run_invoice(document, scoped_deps)

    assert any(flag.code == "stock_exceeded" for flag in result.flags)
    assert "WidgetA" in result.flags[0].message
    assert "999" in result.flags[0].message


def test_checkpointer_persists_state_after_every_node(invoices_dir: Path, deps: Deps) -> None:
    import sqlite3

    from langchain_core.runnables import RunnableConfig
    from langgraph.checkpoint.sqlite import SqliteSaver

    document = load_document(invoices_dir / "invoice_1001.txt")
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    checkpointer = SqliteSaver(conn)

    run_invoice(document, deps, checkpointer=checkpointer)

    config: RunnableConfig = {"configurable": {"thread_id": document.name}}
    history = list(checkpointer.list(config))
    # One checkpoint before the graph starts, plus one after each of the four nodes.
    assert len(history) >= 5
    conn.close()


def test_run_result_never_invents_a_missing_stage(tmp_path: Path, deps: Deps) -> None:
    """If ingestion fails, nothing downstream should have silently produced output."""
    from invoice_automation.extraction import ExtractionFailed

    path = tmp_path / "invoice_unknown.txt"
    path.write_text("not a recorded document", encoding="utf-8")
    document = load_document(path)

    with pytest.raises(LookupError):
        run_invoice(document, deps)
