"""Ticket 14: the dashboard shell served alongside the API.

`web.py` mounts the committed React bundle (`static/`) onto the same FastAPI app
`create_app` builds — these tests check that mount, not the React app itself (there is
no browser here). API routes must win over the static catch-all, and the SPA's
`index.html` must be served for the root path so the client-side router can take over.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite import SqliteSaver

from invoice_automation.api import create_app
from invoice_automation.catalogue import SqliteCatalogue
from invoice_automation.clock import FixedClock
from invoice_automation.deps import Deps
from invoice_automation.payments import RecordingPayment
from invoice_automation.providers import FakeProvider
from invoice_automation.registry import SqliteRegistry
from invoice_automation.web import STATIC_DIR


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
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="dashboard")
    return TestClient(app)


def test_static_dir_holds_a_committed_build() -> None:
    assert (STATIC_DIR / "index.html").is_file()


def test_root_serves_the_dashboard_shell(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<div id=\"root\">" in response.text


def test_api_routes_take_precedence_over_the_static_mount(client: TestClient) -> None:
    response = client.get("/runs")
    assert response.status_code == 200
    assert response.json() == []


def test_built_assets_are_reachable_under_the_dashboard_mount(client: TestClient) -> None:
    asset = next((STATIC_DIR / "assets").iterdir())
    response = client.get(f"/assets/{asset.name}")
    assert response.status_code == 200
