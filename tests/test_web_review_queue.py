"""Ticket 16: the review queue's data needs beyond what tickets 13/15 already expose —
a run's risk score, so a VP can see the compounding-risk number behind an escalation
without recomputing it client-side.

Everything else the queue needs (extracted data, corrections, flags, source document,
decision reasoning, and the approve/reject resume flow) is already covered by
tests/test_web_api.py and tests/test_web_ledger_and_impact.py.
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
from invoice_automation.approval import compute_risk_score
from invoice_automation.catalogue import SqliteCatalogue
from invoice_automation.clock import FixedClock
from invoice_automation.deps import Deps
from invoice_automation.models import Flag
from invoice_automation.payments import RecordingPayment
from invoice_automation.providers import FakeProvider
from invoice_automation.registry import SqliteRegistry

REPO_ROOT = Path(__file__).resolve().parents[1]
CLEAN_INVOICE = REPO_ROOT / "data" / "invoices" / "invoice_1001.txt"


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


@pytest.fixture
def client(deps: Deps, checkpointer: Any, tmp_path: Path) -> TestClient:
    app = create_app(deps, checkpointer, tmp_path / "uploads")
    return TestClient(app)


def test_run_detail_includes_the_risk_score_for_a_clean_invoice(client: TestClient) -> None:
    client.post("/runs", json={"document_path": str(CLEAN_INVOICE)})

    detail = client.get("/runs/invoice_1001.txt").json()
    assert detail["risk_score"] == 0


def test_run_detail_risk_score_matches_the_deterministic_computation(
    deps: Deps, checkpointer: Any, tmp_path: Path
) -> None:
    """Same shape as INV-1008 (test_risk_escalation.py): an unknown vendor plus two
    uncatalogued items compounds past the escalation threshold on soft flags alone,
    $100 under the dollar scrutiny line — no single rule stops it, the accumulated risk
    does."""
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    (responses_dir / "compounding_risk.json").write_text(
        '{"vendor": {"name": "NoProd Industries"}, "line_items": '
        '[{"item": "SuperGizmo", "quantity": 12, "unit_price": "400.00"}, '
        '{"item": "MegaSprocket", "quantity": 6, "unit_price": "850.00"}], '
        '"total": "9900.00"}',
        encoding="utf-8",
    )
    document_dir = tmp_path / "docs"
    document_dir.mkdir()
    document_path = document_dir / "compounding_risk.txt"
    document_path.write_text("irrelevant to the fake", encoding="utf-8")

    scoped = Deps(
        provider=FakeProvider.with_sample_responses(responses_dir),
        catalogue=deps.catalogue,
        payment=deps.payment,
        clock=deps.clock,
        registry=deps.registry,
    )
    client = TestClient(create_app(scoped, checkpointer, tmp_path / "uploads"))

    client.post("/runs", json={"document_path": str(document_path)})

    detail = client.get("/runs/compounding_risk.txt").json()
    flags = [Flag.model_validate(f) for f in detail["flags"]]
    assert detail["risk_score"] == compute_risk_score(flags)
    assert detail["risk_score"] > 0
