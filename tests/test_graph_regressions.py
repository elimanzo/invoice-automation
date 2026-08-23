"""Regressions found reviewing ticket 02.

Both are the same shape: a node returning nothing for a key does not mean "no payment
happened" to LangGraph's checkpointer — it means "leave whatever was there before." On a
persistent checkpointer keyed by document name, "before" can be a different run entirely.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

from invoice_automation.deps import Deps
from invoice_automation.documents import load_document
from invoice_automation.extraction import ExtractionFailed
from invoice_automation.graph import run_invoice


def test_a_rejected_rerun_does_not_inherit_a_prior_runs_payment(
    tmp_path: Path, deps: Deps
) -> None:
    """A prior approved run's payment must not survive onto a later run of the same
    document that is not approved.

    Constructed rather than replayed from a real invoice: reusing the same thread_id
    (the document name) is what exposes the bug, and the fake provider must return a
    different decision on the second call.
    """
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    document_path = tmp_path / "invoice_rerun.txt"
    document_path.write_text("irrelevant to the fake", encoding="utf-8")

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    checkpointer = SqliteSaver(conn)

    (responses_dir / "invoice_rerun.json").write_text(
        '{"vendor": {"name": "Widgets Inc."}, '
        '"line_items": [{"item": "WidgetA", "quantity": 1, "unit_price": "500.00"}], '
        '"total": "500.00"}',
        encoding="utf-8",
    )
    from invoice_automation.providers import FakeProvider

    approved_deps = Deps(
        provider=FakeProvider.with_sample_responses(responses_dir),
        catalogue=deps.catalogue,
        payment=deps.payment,
        clock=deps.clock,
        registry=deps.registry,
    )
    document = load_document(document_path)
    first = run_invoice(document, approved_deps, checkpointer=checkpointer)
    assert first.decision is not None and first.decision.outcome == "approved"
    assert first.payment is not None

    (responses_dir / "invoice_rerun.json").write_text(
        '{"vendor": {"name": "Widgets Inc."}, '
        '"line_items": [{"item": "WidgetA", "quantity": 1, "unit_price": "50000.00"}], '
        '"total": "50000.00"}',
        encoding="utf-8",
    )
    escalated_deps = Deps(
        provider=FakeProvider.with_sample_responses(responses_dir),
        catalogue=deps.catalogue,
        payment=deps.payment,
        clock=deps.clock,
        registry=deps.registry,
    )
    second = run_invoice(document, escalated_deps, checkpointer=checkpointer)

    assert second.decision is not None and second.decision.outcome == "escalated"
    assert second.payment is None, (
        "the escalated re-run must not report the first run's payment as its own"
    )
    conn.close()


def test_a_malformed_provider_response_is_a_clean_cli_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ExtractionFailed must reach the CLI as a handled error, not an unhandled traceback."""
    import invoice_automation.providers as providers_module
    from invoice_automation.cli import main

    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    document_path = tmp_path / "invoice_bad.txt"
    document_path.write_text("irrelevant to the fake", encoding="utf-8")
    # Not a valid invoice payload at all: fails Invoice.model_validate inside extraction.
    (responses_dir / "invoice_bad.json").write_text("{}", encoding="utf-8")

    monkeypatch.setenv("INVOICE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setattr(providers_module, "SAMPLE_RESPONSES_DIR", responses_dir)

    exit_code = main([f"--invoice_path={document_path}"])

    assert exit_code == 1


def test_extraction_failed_propagates_through_the_seam(tmp_path: Path, deps: Deps) -> None:
    """The seam itself must raise ExtractionFailed, not swallow or rewrap it."""
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    document_path = tmp_path / "invoice_bad.txt"
    document_path.write_text("irrelevant to the fake", encoding="utf-8")
    (responses_dir / "invoice_bad.json").write_text("{}", encoding="utf-8")

    from invoice_automation.providers import FakeProvider

    scoped_deps = Deps(
        provider=FakeProvider.with_sample_responses(responses_dir),
        catalogue=deps.catalogue,
        payment=deps.payment,
        clock=deps.clock,
        registry=deps.registry,
    )

    with pytest.raises(ExtractionFailed):
        run_invoice(load_document(document_path), scoped_deps)
