"""Ticket 19: a VP can edit specific fields of an escalated invoice from the review
queue before approving or rejecting. See ADR-0012 for the design — edits and the
outcome travel in one request, are written as human-sourced `Correction`s, and
flags/risk score are recomputed against the edited invoice before the outcome applies.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite import SqliteSaver

from invoice_automation.api import create_app
from invoice_automation.clock import FixedClock
from invoice_automation.catalogue import SqliteCatalogue
from invoice_automation.deps import Deps
from invoice_automation.payments import RecordingPayment
from invoice_automation.providers import FakeProvider
from invoice_automation.registry import SqliteRegistry


@pytest.fixture
def deps(tmp_path: Path) -> Deps:
    return Deps(
        provider=FakeProvider.with_sample_responses(),
        catalogue=SqliteCatalogue(tmp_path / "catalogue.db"),
        payment=RecordingPayment(),
        clock=FixedClock(date(2026, 2, 1)),
        registry=SqliteRegistry(tmp_path / "registry.db"),
    )


@pytest.fixture
def checkpointer() -> Any:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    saver = SqliteSaver(conn)
    yield saver
    conn.close()


def _escalated_run(tmp_path: Path, deps: Deps, stem: str) -> tuple[Deps, Path]:
    """An invoice whose total alone crosses the $10K scrutiny line, clean on every
    other rule — same shape as test_escalation_interrupt_and_resume.py's fixture, so
    escalation is guaranteed regardless of what a test then edits."""
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir(exist_ok=True)
    (responses_dir / f"{stem}.json").write_text(
        '{"vendor": {"name": "Widgets Inc."}, "invoice_date": "2026-01-15", '
        '"due_date": "2026-03-01", "line_items": '
        '[{"item": "WidgetA", "quantity": 15, "unit_price": "250.00"}, '
        '{"item": "WidgetB", "quantity": 10, "unit_price": "500.00"}, '
        '{"item": "GadgetX", "quantity": 5, "unit_price": "750.00"}], '
        '"total": "12500.00"}',
        encoding="utf-8",
    )
    document_dir = tmp_path / "docs"
    document_dir.mkdir(exist_ok=True)
    document_path = document_dir / f"{stem}.txt"
    document_path.write_text("irrelevant to the fake", encoding="utf-8")

    scoped = Deps(
        provider=FakeProvider.with_sample_responses(responses_dir),
        catalogue=deps.catalogue,
        payment=deps.payment,
        clock=deps.clock,
        registry=deps.registry,
    )
    return scoped, document_path


def _make_client(scoped: Deps, checkpointer: Any, tmp_path: Path) -> TestClient:
    return TestClient(create_app(scoped, checkpointer, tmp_path / "uploads"))


def test_editing_a_line_item_before_approving_writes_a_human_correction(
    deps: Deps, checkpointer: Any, tmp_path: Path
) -> None:
    scoped, document_path = _escalated_run(tmp_path, deps, "edit_line_item")
    client = _make_client(scoped, checkpointer, tmp_path)

    client.post("/runs", json={"document_path": str(document_path)})
    assert client.get("/reviews").json()  # sanity: it's actually awaiting review

    response = client.post(
        "/reviews/edit_line_item.txt",
        json={
            "outcome": "approved",
            "reason": "Confirmed the corrected unit price with the vendor.",
            "line_item_edits": [{"index": 0, "field": "unit_price", "value": "300.00"}],
        },
    )
    assert response.status_code == 200
    detail = response.json()

    assert detail["invoice"]["line_items"][0]["unit_price"] == "300.00"

    human_corrections = [c for c in detail["corrections"] if c["source"] == "human"]
    assert len(human_corrections) == 1
    edit_correction = human_corrections[0]
    assert edit_correction["field"] == "line_items[0].unit_price"
    assert edit_correction["raw"] == "250.00"
    assert edit_correction["value"] == "300.00"
    assert edit_correction["confidence"] == 1.0
    assert edit_correction["reason"] == "Confirmed the corrected unit price with the vendor."

    # The rest of the flow (resume, approve, pay) is untouched by the edit.
    assert detail["decision"]["outcome"] == "approved"
    assert scoped.payment.payments  # type: ignore[attr-defined]


def test_editing_a_header_field_recomputes_flags_before_the_outcome_applies(
    deps: Deps, checkpointer: Any, tmp_path: Path
) -> None:
    scoped, document_path = _escalated_run(tmp_path, deps, "edit_due_date")
    client = _make_client(scoped, checkpointer, tmp_path)

    client.post("/runs", json={"document_path": str(document_path)})

    before = client.get("/runs/edit_due_date.txt").json()
    assert not any(f["code"] == "due_date_in_the_past" for f in before["flags"])

    response = client.post(
        "/reviews/edit_due_date.txt",
        json={
            "outcome": "approved",
            "reason": "",
            "header_edits": [{"field": "due_date", "value": "2026-01-01"}],
        },
    )
    assert response.status_code == 200
    detail = response.json()

    assert detail["invoice"]["due_date"] == "2026-01-01"
    assert any(f["code"] == "due_date_in_the_past" for f in detail["flags"])

    human_corrections = [c for c in detail["corrections"] if c["source"] == "human"]
    assert human_corrections[0]["reason"] == "reviewer correction"

    # A new fatal/soft flag caused by the edit itself never blocks approval (ADR-0004's
    # caution ratchet — flags inform the human, they don't gate them).
    assert detail["decision"]["outcome"] == "approved"
    assert scoped.payment.payments  # type: ignore[attr-defined]


def test_editing_an_out_of_range_line_item_index_is_rejected(
    deps: Deps, checkpointer: Any, tmp_path: Path
) -> None:
    scoped, document_path = _escalated_run(tmp_path, deps, "edit_bad_index")
    client = _make_client(scoped, checkpointer, tmp_path)

    client.post("/runs", json={"document_path": str(document_path)})

    response = client.post(
        "/reviews/edit_bad_index.txt",
        json={
            "outcome": "approved",
            "reason": "n/a",
            "line_item_edits": [{"index": 99, "field": "quantity", "value": "1"}],
        },
    )
    assert response.status_code == 422

    # Rejected before anything was resumed — still awaiting review, unresumed.
    detail = client.get("/runs/edit_bad_index.txt").json()
    assert detail["awaiting_review"] is True
    assert detail["decision"]["outcome"] == "escalated"


def test_two_edits_to_the_same_field_chain_raw_through_the_first_edits_value(
    deps: Deps, checkpointer: Any, tmp_path: Path
) -> None:
    """A second edit to a field already staged in this same request overwrites what
    the first edit just wrote, not the original — its Correction's `raw` must reflect
    that, or the audit trail lies about what it overwrote."""
    scoped, document_path = _escalated_run(tmp_path, deps, "duplicate_field_edit")
    client = _make_client(scoped, checkpointer, tmp_path)

    client.post("/runs", json={"document_path": str(document_path)})

    response = client.post(
        "/reviews/duplicate_field_edit.txt",
        json={
            "outcome": "approved",
            "reason": "typo, then typo'd the typo fix",
            "header_edits": [
                {"field": "due_date", "value": "2026-04-01"},
                {"field": "due_date", "value": "2026-05-01"},
            ],
        },
    )
    assert response.status_code == 200
    detail = response.json()

    assert detail["invoice"]["due_date"] == "2026-05-01"
    human_corrections = [c for c in detail["corrections"] if c["source"] == "human"]
    assert len(human_corrections) == 2
    assert human_corrections[0]["raw"] == "2026-03-01"
    assert human_corrections[0]["value"] == "2026-04-01"
    assert human_corrections[1]["raw"] == "2026-04-01"
    assert human_corrections[1]["value"] == "2026-05-01"


def test_submitting_with_no_staged_edits_behaves_exactly_as_before(
    deps: Deps, checkpointer: Any, tmp_path: Path
) -> None:
    scoped, document_path = _escalated_run(tmp_path, deps, "no_edits")
    client = _make_client(scoped, checkpointer, tmp_path)

    client.post("/runs", json={"document_path": str(document_path)})

    response = client.post(
        "/reviews/no_edits.txt",
        json={"outcome": "approved", "reason": "Looks fine."},
    )
    assert response.status_code == 200
    detail = response.json()
    assert all(c["source"] == "model" for c in detail["corrections"])
    assert detail["decision"]["outcome"] == "approved"
