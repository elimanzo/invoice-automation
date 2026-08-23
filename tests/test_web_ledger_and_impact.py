"""Ticket 15: the ledger's richer run summaries, the drill-down's LLM-call trace and
source document, and the `/impact` business-metrics endpoint.

Every field asserted here is additive to what ticket 13 already returns (`RunSummary`,
`RunDetail`) — these tests check the new surface, not the surface tests/test_web_api.py
already covers.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite import SqliteSaver

from invoice_automation.api import create_app
from invoice_automation.catalogue import SqliteCatalogue
from invoice_automation.clock import FixedClock
from invoice_automation.config import MANUAL_COST_PER_INVOICE_USD, MANUAL_PROCESSING_DAYS
from invoice_automation.deps import Deps
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
def client(deps: Deps, checkpointer: Any) -> TestClient:
    app = create_app(deps, checkpointer)
    return TestClient(app)


def _stock_exceeding_client(
    deps: Deps, checkpointer: Any, tmp_path: Path
) -> tuple[TestClient, Path]:
    """invoice_1002.txt's shape, requesting far more GadgetX than the catalogue stocks
    (5, per catalogue.py's seed) — a fatal `stock_exceeded` flag, so the ledger and
    impact tests have a rejected, flagged run to measure alongside the clean one."""
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir(exist_ok=True)
    (responses_dir / "over_stock.json").write_text(
        '{"vendor": {"name": "Gadget Supply Co."}, "line_items": '
        '[{"item": "GadgetX", "quantity": 20, "unit_price": "750.00"}], '
        '"total": "15000.00"}',
        encoding="utf-8",
    )
    document_dir = tmp_path / "docs"
    document_dir.mkdir(exist_ok=True)
    document_path = document_dir / "over_stock.txt"
    document_path.write_text("irrelevant to the fake", encoding="utf-8")

    scoped = Deps(
        provider=FakeProvider.with_sample_responses(responses_dir),
        catalogue=deps.catalogue,
        payment=deps.payment,
        clock=deps.clock,
        registry=deps.registry,
        tracer=deps.tracer,
    )
    app = create_app(scoped, checkpointer)
    return TestClient(app), document_path


def test_run_summary_reports_vendor_correction_count_and_no_severity_when_clean(
    client: TestClient,
) -> None:
    client.post("/runs", json={"document_path": str(CLEAN_INVOICE)})

    run = client.get("/runs").json()[0]
    assert run["vendor"] == "Widgets Inc."
    assert run["correction_count"] == 0
    assert run["max_flag_severity"] is None


def test_run_summary_reports_the_worst_flag_severity(
    deps: Deps, checkpointer: Any, tmp_path: Path
) -> None:
    stock_client, document_path = _stock_exceeding_client(deps, checkpointer, tmp_path)
    stock_client.post("/runs", json={"document_path": str(document_path)})

    run = stock_client.get("/runs").json()[0]
    assert run["max_flag_severity"] == "fatal"
    assert run["outcome"] == "rejected"


def test_run_detail_includes_llm_calls_with_latency_and_tokens(client: TestClient) -> None:
    client.post("/runs", json={"document_path": str(CLEAN_INVOICE)})

    detail = client.get("/runs/invoice_1001.txt").json()
    assert detail["llm_calls"], "extraction should have made at least one LLM call"
    call = detail["llm_calls"][0]
    assert call["latency_ms"] >= 0
    assert call["prompt_tokens"] > 0
    assert call["completion_tokens"] > 0
    assert "cost_usd" in call
    assert "prompt" in call and "response" in call


def test_run_detail_reads_the_persisted_trace_never_re_running(client: TestClient) -> None:
    """A drill-down after the process is already done must not touch the provider
    again — reading `/runs/{name}` twice should not double the recorded LLM calls."""
    client.post("/runs", json={"document_path": str(CLEAN_INVOICE)})

    first = client.get("/runs/invoice_1001.txt").json()
    second = client.get("/runs/invoice_1001.txt").json()
    assert len(first["llm_calls"]) == len(second["llm_calls"])


def test_run_detail_includes_the_source_document_alongside_the_extraction(
    client: TestClient,
) -> None:
    client.post("/runs", json={"document_path": str(CLEAN_INVOICE)})

    detail = client.get("/runs/invoice_1001.txt").json()
    assert detail["document_format"] == "text"
    assert "Widgets Inc." in detail["raw_text"]


def test_impact_reports_business_metrics_from_config(
    deps: Deps, checkpointer: Any, tmp_path: Path
) -> None:
    app = create_app(deps, checkpointer)
    client = TestClient(app)
    client.post("/runs", json={"document_path": str(CLEAN_INVOICE)})

    stock_client, document_path = _stock_exceeding_client(deps, checkpointer, tmp_path)
    stock_client.post("/runs", json={"document_path": str(document_path)})

    impact = client.get("/impact").json()
    assert impact["invoices_processed"] == 2
    assert impact["manual_baseline_days"] == float(MANUAL_PROCESSING_DAYS)
    assert impact["manual_cost_per_invoice_usd"] == str(MANUAL_COST_PER_INVOICE_USD)
    assert impact["avg_processing_ms"] > 0
    assert impact["errors_caught"] >= 1
    assert Decimal(impact["dollars_flagged"]) >= Decimal("15000.00")
    assert Decimal(impact["cost_per_invoice_usd"]) > 0


def test_impact_with_no_runs_reports_zeros_not_an_error(client: TestClient) -> None:
    impact = client.get("/impact").json()
    assert impact["invoices_processed"] == 0
    assert impact["avg_processing_ms"] == 0
    assert impact["errors_caught"] == 0
    assert Decimal(impact["dollars_flagged"]) == Decimal("0")
    assert Decimal(impact["cost_per_invoice_usd"]) == Decimal("0")
