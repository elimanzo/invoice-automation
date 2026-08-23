"""Ticket 12: every LLM call recorded and costed, a persisted trace per run, a
content-hash cache that spares a repeated development run its provider calls, and
human overrides captured as labelled data.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
import sqlite3

from invoice_automation.cache import InMemoryCache, NullCache, SqliteCache, hash_request
from invoice_automation.deps import Deps
from invoice_automation.documents import load_document
from invoice_automation.graph import resume_invoice, run_invoice
from invoice_automation.models import HumanReview
from invoice_automation.overrides import (
    InMemoryOverrideStore,
    Override,
    build_override_report,
)
from invoice_automation.tracing import InMemoryTracer, SqliteTracer


def test_processing_the_same_document_twice_issues_one_real_provider_call(
    invoices_dir: Path, deps: Deps
) -> None:
    cached_deps = replace(deps, cache=InMemoryCache())
    document = load_document(invoices_dir / "invoice_1001.txt")

    run_invoice(document, cached_deps)
    calls_after_first = len(cached_deps.provider.calls)  # type: ignore[attr-defined]
    assert calls_after_first > 0

    run_invoice(document, cached_deps)
    assert len(cached_deps.provider.calls) == calls_after_first  # type: ignore[attr-defined]


def test_cache_disabled_by_config_issues_a_real_call_every_time(
    invoices_dir: Path, deps: Deps
) -> None:
    uncached_deps = replace(deps, cache=NullCache())
    document = load_document(invoices_dir / "invoice_1001.txt")

    run_invoice(document, uncached_deps)
    calls_after_first = len(uncached_deps.provider.calls)  # type: ignore[attr-defined]

    run_invoice(document, uncached_deps)
    assert len(uncached_deps.provider.calls) > calls_after_first  # type: ignore[attr-defined]


def test_cache_hits_are_recorded_in_the_trace_distinct_from_real_calls(
    invoices_dir: Path, deps: Deps
) -> None:
    cached_deps = replace(deps, cache=InMemoryCache(), tracer=InMemoryTracer())
    document = load_document(invoices_dir / "invoice_1001.txt")

    run_invoice(document, cached_deps)
    run_invoice(document, cached_deps)

    calls = cached_deps.tracer.llm_calls_for(document.name)
    assert any(not c.cache_hit for c in calls)
    assert any(c.cache_hit for c in calls)


def test_a_persisted_trace_has_one_entry_per_stage_executed(
    invoices_dir: Path, deps: Deps, tmp_path: Path
) -> None:
    tracer = SqliteTracer(tmp_path / "trace.db")
    traced_deps = replace(deps, tracer=tracer)
    document = load_document(invoices_dir / "invoice_1001.txt")

    run_invoice(document, traced_deps)

    stages = [s.stage for s in tracer.stages_for(document.name)]
    # invoice_1001 is a clean, under-threshold invoice: approved and paid, so it
    # executes every stage except await_review, each exactly once.
    assert stages == ["ingest", "reconcile", "validate", "approve", "pay"]


def test_await_review_is_recorded_only_once_the_run_actually_resumes(
    invoices_dir: Path, deps: Deps, tmp_path: Path
) -> None:
    from tests.test_escalation_interrupt_and_resume import _document, _escalating_deps

    tracer = SqliteTracer(tmp_path / "trace.db")
    scoped = replace(
        _escalating_deps(deps, tmp_path, "invoice_escalated_trace"), tracer=tracer
    )
    document = _document(tmp_path, "invoice_escalated_trace")

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    try:
        escalated = run_invoice(document, scoped, checkpointer=checkpointer)
        assert escalated.decision is not None and escalated.decision.outcome == "escalated"
        assert "await_review" not in {s.stage for s in tracer.stages_for(document.name)}

        resume_invoice(
            document.name,
            HumanReview(outcome="approved", reason="Confirmed."),
            scoped,
            checkpointer=checkpointer,
        )
        stages = [s.stage for s in tracer.stages_for(document.name)]
        assert stages.count("await_review") == 1
    finally:
        conn.close()


def test_llm_call_records_prompt_and_response_verbatim(
    invoices_dir: Path, deps: Deps
) -> None:
    tracer = InMemoryTracer()
    traced_deps = replace(deps, tracer=tracer)
    document = load_document(invoices_dir / "invoice_1001.txt")

    run_invoice(document, traced_deps)

    calls = tracer.llm_calls_for(document.name)
    assert calls
    extract_call = next(c for c in calls if c.kind == "extract")
    assert document.raw_text in extract_call.prompt
    assert extract_call.response  # the model's JSON payload, not just its size


def test_llm_call_cost_aggregates_per_invoice(invoices_dir: Path, deps: Deps) -> None:
    tracer = InMemoryTracer()
    traced_deps = replace(deps, tracer=tracer)
    document = load_document(invoices_dir / "invoice_1001.txt")

    run_invoice(document, traced_deps)

    assert tracer.cost_for(document.name) > Decimal("0")


def test_hash_request_is_stable_regardless_of_key_order() -> None:
    a = hash_request({"system": "x", "user": "y"})
    b = hash_request({"user": "y", "system": "x"})
    assert a == b


def test_sqlite_cache_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "cache.db"
    SqliteCache(path).set("k", {"response": {"a": 1}})
    assert SqliteCache(path).get("k") == {"response": {"a": 1}}


def test_an_override_is_retrievable_as_labelled_data_with_both_decisions_and_reason(
    invoices_dir: Path, deps: Deps, tmp_path: Path
) -> None:
    from tests.test_escalation_interrupt_and_resume import _document, _escalating_deps

    overrides = InMemoryOverrideStore()
    scoped = replace(
        _escalating_deps(deps, tmp_path, "invoice_override_me"), overrides=overrides
    )
    document = _document(tmp_path, "invoice_override_me")

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    try:
        run_invoice(document, scoped, checkpointer=checkpointer)
        resume_invoice(
            document.name,
            HumanReview(outcome="approved", reason="Confirmed with the requester."),
            scoped,
            checkpointer=checkpointer,
        )
    finally:
        conn.close()

    recorded = overrides.list()
    assert len(recorded) == 1
    override = recorded[0]
    assert override.document_name == document.name
    assert override.system_outcome == "escalated"
    assert override.human_outcome == "approved"
    assert override.reason == "Confirmed with the requester."


def test_override_report_surfaces_escalation_rate_and_human_approved_share() -> None:
    overrides = [
        Override(
            document_name="a",
            system_outcome="escalated",
            system_reasoning="over threshold",
            human_outcome="approved",
            reason="fine",
        ),
        Override(
            document_name="b",
            system_outcome="escalated",
            system_reasoning="risk score",
            human_outcome="rejected",
            reason="bad vendor",
        ),
    ]

    report = build_override_report(overrides, total_runs=10)

    assert report.total_escalations == 2
    assert report.escalation_rate == pytest.approx(0.2)
    assert report.approved_count == 1
    assert report.approved_share == pytest.approx(0.5)
    assert [o.document_name for o in report.disagreements] == ["a"]
