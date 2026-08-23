"""Ticket 18: the "recorded" test layer (ADR-0006's middle layer) — the real graph
against real, recorded Grok output, replayed with no network and no key.

`tests/cassettes/` holds request/response pairs captured once by
`scripts/record_cassettes.py` against the live API. Loading them here through
`FakeProvider.with_sample_responses` — the exact same loader the always-available fake
provider uses — is the whole point: cassette replay and the fake provider are one
implementation, not two.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from invoice_automation.batch import run_batch
from invoice_automation.deps import Deps
from invoice_automation.evals import GOLDEN_EXTRA_DIR, GOLDEN_INVOICES_DIR
from invoice_automation.providers import FakeProvider

REPO_ROOT = Path(__file__).resolve().parent.parent
CASSETTES_DIR = REPO_ROOT / "tests" / "cassettes"


@pytest.fixture
def cassette_deps(deps: Deps) -> Deps:
    """The shared `deps` fixture, with its provider swapped for one that replays the
    recorded cassettes instead of the package's hand-written sample responses."""
    return replace(deps, provider=FakeProvider.with_sample_responses(CASSETTES_DIR))


def test_cassettes_directory_covers_every_document_that_calls_the_model() -> None:
    """Per ADR-0009, only text and PDF documents ever reach `structured()` — JSON,
    CSV, and XML are parsed with no provider call, so there is nothing to cassette for
    them. Every text/PDF document in the golden set must have a recording."""
    model_read_documents = sorted(
        p.name
        for directory in (GOLDEN_INVOICES_DIR, GOLDEN_EXTRA_DIR)
        for p in directory.iterdir()
        if p.suffix in (".txt", ".pdf")
        # No text layer at all — fails in `load_document` before any provider call is
        # even built (test_authored_fixtures.py asserts this directly).
        and p.name != "invoice_9004_image_only.pdf"
    )
    recorded_stems = {p.stem for p in CASSETTES_DIR.glob("*.json") if p.stem != "_manifest"}
    missing = [
        name for name in model_read_documents if Path(name).stem not in recorded_stems
    ]
    assert missing == []


def test_the_golden_set_runs_end_to_end_on_recorded_responses_alone(
    cassette_deps: Deps,
) -> None:
    """The real graph, against real recorded model output, no network: every document
    in `data/invoices/` either reaches a decision or fails with a documented reason
    (invoice_9004's image-only PDF has no text layer at all and never calls the
    provider) — nothing raises `MissingRecording`."""
    for directory in (GOLDEN_INVOICES_DIR, GOLDEN_EXTRA_DIR):
        summary = run_batch(directory, cassette_deps)
        assert len(summary.items) > 0
        for item in summary.items:
            assert item.result is not None or item.error is not None


def test_cassettes_still_match_the_current_prompts_and_schemas() -> None:
    """The other silent-failure mode ADR-0006 and this ticket call out: a cassette
    that still loads and still returns well-formed JSON, but for a prompt or schema
    the code no longer builds — `scripts/check_cassettes_fresh.py` fingerprints every
    request the current code makes and compares it against the fingerprint recorded
    alongside each cassette."""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_cassettes_fresh.py")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
