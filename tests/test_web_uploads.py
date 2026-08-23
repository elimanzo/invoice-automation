"""Ticket 20: a clerk-facing alternative to path-based `/runs`.

`/uploads` is a thin wrapper over the same seam `directory_path` already uses
(`run_batch`) — these tests exercise the upload-specific behavior (validation before
anything reaches disk, per-request storage isolation) rather than re-testing batch
fault-isolation itself, which `tests/test_graph_regressions.py` already covers.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite import SqliteSaver

from invoice_automation.api import MAX_UPLOAD_BYTES, create_app
from invoice_automation.catalogue import SqliteCatalogue
from invoice_automation.clock import FixedClock
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
def uploads_dir(tmp_path: Path) -> Path:
    return tmp_path / "uploads"


@pytest.fixture
def client(deps: Deps, checkpointer: Any, uploads_dir: Path) -> TestClient:
    app = create_app(deps, checkpointer, uploads_dir)
    return TestClient(app)


def test_upload_single_file_processes_and_appears_as_a_run(client: TestClient) -> None:
    resp = client.post(
        "/uploads",
        files=[("files", ("invoice_1001.txt", CLEAN_INVOICE.read_bytes(), "text/plain"))],
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["accepted"] == ["invoice_1001.txt"]
    assert body["rejected"] == []

    runs = client.get("/runs").json()
    assert len(runs) == 1
    assert runs[0]["document_name"] == "invoice_1001.txt"
    assert runs[0]["outcome"] == "approved"


def test_upload_rejects_unsupported_extension_without_blocking_good_file(
    client: TestClient,
) -> None:
    resp = client.post(
        "/uploads",
        files=[
            ("files", ("invoice_1001.txt", CLEAN_INVOICE.read_bytes(), "text/plain")),
            ("files", ("virus.exe", b"not an invoice", "application/octet-stream")),
        ],
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["accepted"] == ["invoice_1001.txt"]
    assert len(body["rejected"]) == 1
    assert body["rejected"][0]["filename"] == "virus.exe"
    assert "supported" in body["rejected"][0]["reason"].lower()

    runs = client.get("/runs").json()
    assert len(runs) == 1
    assert runs[0]["document_name"] == "invoice_1001.txt"


def test_upload_rejects_oversized_file_before_writing_to_disk(
    client: TestClient, uploads_dir: Path
) -> None:
    oversized = b"x" * (MAX_UPLOAD_BYTES + 1)
    resp = client.post(
        "/uploads",
        files=[("files", ("too_big.txt", oversized, "text/plain"))],
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["accepted"] == []
    assert body["rejected"][0]["filename"] == "too_big.txt"
    assert "MB" in body["rejected"][0]["reason"]

    # Never written: rejected before the batch directory is even created.
    assert not uploads_dir.exists() or not any(uploads_dir.rglob("too_big.txt"))
    assert client.get("/runs").json() == []


def test_upload_rejects_when_no_files_sent(client: TestClient) -> None:
    resp = client.post("/uploads", files=[])
    assert resp.status_code == 422


def test_upload_rejects_a_path_traversal_filename(client: TestClient, uploads_dir: Path) -> None:
    resp = client.post(
        "/uploads",
        files=[
            (
                "files",
                ("../../../../evil.txt", b"not an invoice", "text/plain"),
            )
        ],
    )
    assert resp.status_code == 202
    body = resp.json()
    # "evil.txt" survives Path(...).name as a real, supported extension, so it's
    # accepted — the point of the fix is where it lands, not whether it's allowed.
    assert body["accepted"] == ["evil.txt"]
    assert body["rejected"] == []
    assert not (uploads_dir.parent.parent / "evil.txt").exists()
    stored = list(uploads_dir.rglob("evil.txt"))
    assert len(stored) == 1
    assert uploads_dir in stored[0].parents


def test_upload_rejects_duplicate_filenames_within_one_request(client: TestClient) -> None:
    resp = client.post(
        "/uploads",
        files=[
            ("files", ("invoice_1001.txt", CLEAN_INVOICE.read_bytes(), "text/plain")),
            ("files", ("invoice_1001.txt", b"a different file, same name", "text/plain")),
        ],
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["accepted"] == ["invoice_1001.txt"]
    assert len(body["rejected"]) == 1
    assert body["rejected"][0]["filename"] == "invoice_1001.txt"
    assert "duplicate" in body["rejected"][0]["reason"].lower()


def test_two_upload_requests_with_the_same_filename_do_not_collide_on_disk(
    client: TestClient, uploads_dir: Path
) -> None:
    for _ in range(2):
        resp = client.post(
            "/uploads",
            files=[("files", ("invoice_1001.txt", CLEAN_INVOICE.read_bytes(), "text/plain"))],
        )
        assert resp.status_code == 202

    stored = list(uploads_dir.rglob("invoice_1001.txt"))
    assert len(stored) == 2
    batch_dirs = {path.parent for path in stored}
    assert len(batch_dirs) == 2
