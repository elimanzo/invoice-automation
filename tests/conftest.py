from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from invoice_automation.catalogue import SqliteCatalogue
from invoice_automation.clock import FixedClock
from invoice_automation.deps import Deps
from invoice_automation.payments import RecordingPayment
from invoice_automation.providers import FakeProvider
from invoice_automation.registry import SqliteRegistry

REPO_ROOT = Path(__file__).resolve().parents[1]
INVOICES = REPO_ROOT / "data" / "invoices"


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings.from_env() loads a real .env file if one exists (for CLI/dashboard
    convenience) — but a test's `monkeypatch.delenv(...)` simulating "no key set" must
    not be silently undone by a developer's actual .env on disk. Tests control the
    environment directly; the file is never part of what a test result can depend on."""
    monkeypatch.setattr("invoice_automation.config.load_dotenv", lambda *a, **k: None)


@pytest.fixture
def invoices_dir() -> Path:
    return INVOICES


@pytest.fixture
def deps(tmp_path: Path) -> Deps:
    """A fully in-test dependency set: no network, no key, no shared state."""
    return Deps(
        provider=FakeProvider.with_sample_responses(),
        catalogue=SqliteCatalogue(tmp_path / "catalogue.db"),
        payment=RecordingPayment(),
        clock=FixedClock(date(2026, 2, 1)),
        registry=SqliteRegistry(tmp_path / "registry.db"),
    )
