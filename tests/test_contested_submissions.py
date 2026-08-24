"""Two documents in one batch claiming to be the current version of one invoice.

The defect these cover: `run_batch` iterates a directory in sorted order, so
`invoice_1004.json` ($1,890) reached approval, auto-approved, and paid before
`invoice_1004_revised.json` ($5,940) was ever read. The revision then arrived to a
settled obligation and raised `RevisionAfterPayment` — correct, but too late; $1,890 had
already left against an invoice that was never for $1,890. Filename order was deciding
which total Acme paid.

Each test drives `run_batch`, not `contested_documents` alone, because what matters is
what gets paid — the pre-scan is only interesting insofar as it changes that. The
`_a`/`_b` filename prefixes in the order-independence test are load-bearing: they are how
a copy of the pair is made to reach the batch in the opposite sequence.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from invoice_automation.batch import run_batch
from invoice_automation.contested import CONTESTED_FLAG_CODE, contested_documents
from invoice_automation.deps import Deps
from invoice_automation.documents import load_document
from invoice_automation.payments import RecordingPayment

REPO_ROOT = Path(__file__).resolve().parents[1]
INVOICES = REPO_ROOT / "data" / "invoices"

_PAIR = ("invoice_1004.json", "invoice_1004_revised.json")


def _batch_dir(destination: Path, names: dict[str, str]) -> Path:
    """A directory holding only `names` — source filename mapped to the name it takes in
    the batch, so a test can control the order the pair is processed in."""
    destination.mkdir(parents=True, exist_ok=True)
    for source, target in names.items():
        shutil.copy(INVOICES / source, destination / target)
    return destination


def test_neither_version_pays_when_a_revision_contests_the_original(
    deps: Deps, tmp_path: Path
) -> None:
    """The real sample data. Both documents reach a human; the mock payment function is
    never called. Escalated, not rejected — one of these two totals is almost certainly
    owed, and the system's job is to stop guessing which, not to refuse the invoice."""
    directory = _batch_dir(tmp_path / "batch", {name: name for name in _PAIR})

    summary = run_batch(directory, deps)

    outcomes = {item.document_name: item.outcome for item in summary.items}
    assert outcomes == {
        "invoice_1004.json": "escalated",
        "invoice_1004_revised.json": "escalated",
    }

    payment = deps.payment
    assert isinstance(payment, RecordingPayment)
    assert payment.payments == []


def test_holding_both_versions_does_not_depend_on_filename_order(
    deps: Deps, tmp_path: Path
) -> None:
    """The bug's actual mechanism: reverse the order the pair arrives in and the outcome
    must not move. Under the old behaviour this batch paid $5,940 first and then rejected
    the $1,890 original as a stale duplicate — a different answer for the same two
    documents, decided by nothing but the sort."""
    directory = _batch_dir(
        tmp_path / "reversed",
        {"invoice_1004_revised.json": "a_revision.json", "invoice_1004.json": "b_original.json"},
    )

    summary = run_batch(directory, deps)

    assert [item.document_name for item in summary.items] == ["a_revision.json", "b_original.json"]
    assert {item.outcome for item in summary.items} == {"escalated"}

    payment = deps.payment
    assert isinstance(payment, RecordingPayment)
    assert payment.payments == []


def test_the_hold_is_explained_on_both_documents(deps: Deps, tmp_path: Path) -> None:
    """A reviewer opening either document must see why it stopped, naming the other
    document — an escalation whose reason is "escalated" tells a human nothing."""
    directory = _batch_dir(tmp_path / "batch", {name: name for name in _PAIR})

    summary = run_batch(directory, deps)

    for item in summary.items:
        assert item.result is not None
        codes = [flag.code for flag in item.result.flags]
        assert CONTESTED_FLAG_CODE in codes

        message = next(f.message for f in item.result.flags if f.code == CONTESTED_FLAG_CODE)
        other = next(name for name in _PAIR if name != item.document_name)
        assert other in message
        assert "1004" in message


def test_a_duplicate_with_no_revision_is_not_contested(tmp_path: Path) -> None:
    """The pre-scan must stay narrow. Two documents for one invoice with no revision
    between them are an ordinary duplicate or enrichment, which reconciliation already
    resolves correctly whichever arrives first — holding those for a human would turn a
    solved case into manual work, which is the cost this whole system exists to remove."""
    payload = json.loads((INVOICES / "invoice_1004.json").read_text(encoding="utf-8"))
    payload.pop("revision", None)

    directory = tmp_path / "plain-duplicate"
    directory.mkdir()
    for name in ("first.json", "second.json"):
        (directory / name).write_text(json.dumps(payload), encoding="utf-8")

    assert contested_documents(sorted(directory.iterdir())) == {}


def test_a_lone_revision_is_not_contested(tmp_path: Path) -> None:
    """A revision with nothing to contest is just an invoice. Only a collision within one
    batch triggers the hold — the version it supersedes arriving in some *earlier* batch
    is reconciliation's case (and `RevisionAfterPayment`'s), not this one's."""
    directory = _batch_dir(tmp_path / "lone", {"invoice_1004_revised.json": "invoice_1004_revised.json"})

    assert contested_documents(sorted(directory.iterdir())) == {}


def test_documents_needing_the_model_are_skipped_not_guessed_at(tmp_path: Path) -> None:
    """The documented limitation (ADR-0013): a `.txt` document's invoice number is only
    known after extraction, so the pre-scan cannot see it and says so by omission rather
    than by guessing from the filename. Such a revision is still caught by
    reconciliation, one document later."""
    directory = _batch_dir(
        tmp_path / "text",
        {"invoice_1004.json": "invoice_1004.json", "invoice_1001.txt": "invoice_1004_also.txt"},
    )

    assert contested_documents(sorted(directory.iterdir())) == {}

def test_two_copies_of_one_revision_are_not_contested(deps: Deps, tmp_path: Path) -> None:
    """Regression (code review of ADR-0013): the rule is *disagreement*, not "a revision
    is present". Two copies of the same revision make the same claim twice — an ordinary
    duplicate, which reconciliation drops. Holding them would block the unambiguously
    current version of the invoice from ever paying, which is strictly worse than the bug
    the pre-scan exists to fix."""
    directory = _batch_dir(
        tmp_path / "same-claim",
        {"invoice_1004_revised.json": "copy_a.json"},
    )
    shutil.copy(INVOICES / "invoice_1004_revised.json", directory / "copy_b.json")

    assert contested_documents(sorted(directory.iterdir())) == {}

    summary = run_batch(directory, deps)

    payment = deps.payment
    assert isinstance(payment, RecordingPayment)
    assert len(payment.payments) == 1  # paid once, for the revision's total
    assert {item.outcome for item in summary.items} == {"approved"}


def test_two_different_revisions_of_one_invoice_are_contested(deps: Deps, tmp_path: Path) -> None:
    """The other side of the same rule: two documents both claiming to be the revision,
    with different labels, disagree as sharply as a revision and an original do."""
    payload = json.loads((INVOICES / "invoice_1004_revised.json").read_text(encoding="utf-8"))
    directory = tmp_path / "two-revisions"
    directory.mkdir()
    for name, revision in (("r1.json", "R1"), ("r2.json", "R2")):
        (directory / name).write_text(json.dumps({**payload, "revision": revision}), encoding="utf-8")

    assert set(contested_documents(sorted(directory.iterdir()))) == {"r1.json", "r2.json"}

    summary = run_batch(directory, deps)

    payment = deps.payment
    assert isinstance(payment, RecordingPayment)
    assert payment.payments == []
    assert {item.outcome for item in summary.items} == {"escalated"}


def test_the_pre_scan_never_extracts_pdf_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A PDF cannot be a structured format, so the pre-scan must not pay for its text
    extraction — the batch loop loads it again a moment later, and pdfplumber is the
    slowest step in the whole run."""
    directory = _batch_dir(
        tmp_path / "with-pdf",
        {"invoice_1004.json": "invoice_1004.json", "invoice_1011.pdf": "invoice_1011.pdf"},
    )

    loaded: list[str] = []

    def _counting_load(path: Path) -> Any:
        loaded.append(path.name)
        return load_document(path)

    monkeypatch.setattr("invoice_automation.contested.load_document", _counting_load)
    contested_documents(sorted(directory.iterdir()))

    assert loaded == ["invoice_1004.json"]
