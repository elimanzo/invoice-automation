"""Ticket 13: the pipeline reachable over HTTP.

Every endpoint delegates to `run_invoice` / `resume_invoice` / `run_batch` (graph.py,
batch.py) — the same primary seam and resume entry point the CLI uses — so these tests
exercise the API layer itself, not the pipeline logic already covered elsewhere.

Most endpoints are exercised with FastAPI's `TestClient`, which runs the whole ASGI app
(including its `BackgroundTasks`) to completion before a call returns — so a trigger's
processing has already finished by the time the response comes back, no polling needed.
The one exception is the SSE stream: `TestClient`'s in-process ASGI transport buffers a
response fully before returning it, which can never work for a live, unbounded stream.
That test spins up a real `uvicorn` server on a background thread and connects over an
actual socket, matching the ticket's "tested over real HTTP" instruction literally.
"""

from __future__ import annotations

import socket
import sqlite3
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any, Iterator

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite import SqliteSaver

from invoice_automation.api import create_app
from invoice_automation.catalogue import SqliteCatalogue
from invoice_automation.clock import FixedClock
from invoice_automation.deps import Deps
from invoice_automation.payments import RecordingPayment
from invoice_automation.providers import FakeProvider
from invoice_automation.registry import SqliteRegistry

REPO_ROOT = Path(__file__).resolve().parents[1]
CLEAN_INVOICE = REPO_ROOT / "data" / "invoices" / "invoice_1001.txt"


@pytest.fixture
def api_deps(tmp_path: Path) -> Deps:
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
def client(api_deps: Deps, checkpointer: Any, tmp_path: Path) -> TestClient:
    app = create_app(api_deps, checkpointer, tmp_path / "uploads")
    return TestClient(app)


def _escalating_deps(deps: Deps, tmp_path: Path, document_stem: str) -> Deps:
    """A document whose total alone crosses the $10K scrutiny line — same fixture
    shape as test_escalation_interrupt_and_resume.py, kept local since the API's
    tests need their own tmp_path-scoped response and document files."""
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
    document_dir = tmp_path / "docs"
    document_dir.mkdir(exist_ok=True)
    (document_dir / f"{document_stem}.txt").write_text("irrelevant to the fake", encoding="utf-8")

    return Deps(
        provider=FakeProvider.with_sample_responses(responses_dir),
        catalogue=deps.catalogue,
        payment=deps.payment,
        clock=deps.clock,
        registry=deps.registry,
    )


def _escalating_client(
    deps: Deps, checkpointer: Any, tmp_path: Path
) -> tuple[TestClient, Deps, Path]:
    scoped = _escalating_deps(deps, tmp_path, "escalate_me")
    app = create_app(scoped, checkpointer, tmp_path / "uploads")
    return TestClient(app), scoped, tmp_path / "docs" / "escalate_me.txt"


def test_trigger_and_list_runs_reports_identity_decision_amount_and_flags(
    client: TestClient,
) -> None:
    resp = client.post("/runs", json={"document_path": str(CLEAN_INVOICE)})
    assert resp.status_code == 202
    assert resp.json()["document_name"] == "invoice_1001.txt"

    runs = client.get("/runs").json()
    assert len(runs) == 1
    run = runs[0]
    assert run["document_name"] == "invoice_1001.txt"
    assert run["outcome"] == "approved"
    assert run["amount"] == "5000.00"
    assert run["flag_count"] == 0
    assert run["timestamp"] is not None


def test_get_run_returns_full_detail(client: TestClient) -> None:
    client.post("/runs", json={"document_path": str(CLEAN_INVOICE)})

    detail = client.get("/runs/invoice_1001.txt")
    assert detail.status_code == 200
    body = detail.json()
    assert body["invoice"]["invoice_number"] == "INV-1001"
    assert body["decision"]["outcome"] == "approved"
    assert body["awaiting_review"] is False
    assert any(stage["stage"] == "pay" for stage in body["stages"])


def test_requesting_an_unknown_run_returns_clean_not_found(client: TestClient) -> None:
    resp = client.get("/runs/does-not-exist.txt")
    assert resp.status_code == 404


def test_trigger_rejects_ambiguous_or_empty_body(client: TestClient) -> None:
    assert client.post("/runs", json={}).status_code == 422
    assert (
        client.post(
            "/runs",
            json={"document_path": str(CLEAN_INVOICE), "directory_path": "data/invoices"},
        ).status_code
        == 422
    )


def test_trigger_with_unknown_document_returns_a_clean_client_error(client: TestClient) -> None:
    resp = client.post("/runs", json={"document_path": "no/such/file.txt"})
    assert resp.status_code == 400


def test_trigger_directory_processes_every_document(
    client: TestClient, tmp_path: Path
) -> None:
    docs_dir = tmp_path / "batch"
    docs_dir.mkdir()
    (docs_dir / "a.txt").write_text(CLEAN_INVOICE.read_text(encoding="utf-8"), encoding="utf-8")

    resp = client.post("/runs", json={"directory_path": str(docs_dir)})
    assert resp.status_code == 202

    runs = client.get("/runs").json()
    assert any(r["document_name"] == "a.txt" for r in runs)


def test_list_reviews_shows_only_escalated_runs_awaiting_a_decision(
    api_deps: Deps, checkpointer: Any, tmp_path: Path
) -> None:
    escalating_client, _, escalate_doc = _escalating_client(api_deps, checkpointer, tmp_path)

    escalating_client.post("/runs", json={"document_path": str(escalate_doc)})

    reviews = escalating_client.get("/reviews").json()
    assert len(reviews) == 1
    assert reviews[0]["document_name"] == "escalate_me.txt"
    assert reviews[0]["outcome"] == "escalated"


def test_submitting_an_approval_over_http_resumes_the_run_and_results_in_payment(
    api_deps: Deps, checkpointer: Any, tmp_path: Path
) -> None:
    escalating_client, scoped, escalate_doc = _escalating_client(api_deps, checkpointer, tmp_path)
    escalating_client.post("/runs", json={"document_path": str(escalate_doc)})
    assert escalating_client.get("/reviews").json()  # confirms it is actually paused

    resp = escalating_client.post(
        "/reviews/escalate_me.txt",
        json={"outcome": "approved", "reason": "Confirmed with the requester."},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"]["outcome"] == "approved"
    assert body["human_review"] == {
        "outcome": "approved",
        "reason": "Confirmed with the requester.",
    }
    assert len(scoped.payment.payments) == 1  # type: ignore[attr-defined]
    assert escalating_client.get("/reviews").json() == []


def test_submitting_a_rejection_over_http_resumes_without_paying(
    api_deps: Deps, checkpointer: Any, tmp_path: Path
) -> None:
    escalating_client, scoped, escalate_doc = _escalating_client(api_deps, checkpointer, tmp_path)
    escalating_client.post("/runs", json={"document_path": str(escalate_doc)})

    resp = escalating_client.post(
        "/reviews/escalate_me.txt",
        json={"outcome": "rejected", "reason": "Vendor could not be verified."},
    )

    assert resp.status_code == 200
    assert resp.json()["decision"]["outcome"] == "rejected"
    assert scoped.payment.payments == []  # type: ignore[attr-defined]


def test_resuming_an_already_resumed_run_returns_a_conflict(
    api_deps: Deps, checkpointer: Any, tmp_path: Path
) -> None:
    escalating_client, _, escalate_doc = _escalating_client(api_deps, checkpointer, tmp_path)
    escalating_client.post("/runs", json={"document_path": str(escalate_doc)})
    escalating_client.post(
        "/reviews/escalate_me.txt", json={"outcome": "approved", "reason": "Cleared."}
    )

    resp = escalating_client.post(
        "/reviews/escalate_me.txt", json={"outcome": "approved", "reason": "Again."}
    )
    assert resp.status_code == 409


def test_resuming_a_run_that_was_never_escalated_returns_a_conflict(client: TestClient) -> None:
    client.post("/runs", json={"document_path": str(CLEAN_INVOICE)})

    resp = client.post(
        "/reviews/invoice_1001.txt", json={"outcome": "approved", "reason": "n/a"}
    )
    assert resp.status_code == 409


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return int(port)


@pytest.fixture
def live_server(api_deps: Deps, checkpointer: Any, tmp_path: Path) -> Iterator[str]:
    """A real uvicorn server on a background thread, for the one test that needs
    actual socket-level streaming rather than TestClient's fully-buffered transport."""
    app = create_app(api_deps, checkpointer, tmp_path / "uploads")
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=5)


def test_sse_stream_reports_stage_transitions_as_processing_proceeds(
    live_server: str,
) -> None:
    received: list[str] = []

    def read_stream() -> None:
        with httpx.Client(base_url=live_server, timeout=10) as stream_client:
            with stream_client.stream("GET", "/events") as response:
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    received.append(line)
                    if len(received) >= 8:  # ingest..pay, enter+leave, is plenty
                        break

    reader = threading.Thread(target=read_stream, daemon=True)
    reader.start()
    time.sleep(0.3)  # let the subscription register before anything is published

    with httpx.Client(base_url=live_server) as trigger_client:
        trigger_client.post("/runs", json={"document_path": str(CLEAN_INVOICE)})

    reader.join(timeout=10)
    assert not reader.is_alive()
    # The bus publishes a stage's "leave" event a hair before that stage's own
    # structured-log line is written on the server's worker thread; give it a moment
    # to finish before this test (and pytest's stream capture for it) tears down.
    time.sleep(0.2)
    assert received
    assert any('"stage": "ingest"' in line for line in received)
    assert any('"transition": "enter"' in line for line in received)
    assert any('"transition": "leave"' in line for line in received)
