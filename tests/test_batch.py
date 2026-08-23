"""Batch mode: every document in a directory, sequential, one bad document never stops
the rest, and re-running the same directory never pays anything twice.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from invoice_automation.batch import run_batch
from invoice_automation.deps import Deps
from invoice_automation.providers import StructuredCall


class _DirectoryScriptedProvider:
    """Keyed by document stem, like FakeProvider, but raises for anything unscripted
    instead of the real provider's document-not-found message — irrelevant here, since
    every test controls exactly which documents exist in its own tmp_path directory."""

    def __init__(self, responses: dict[str, dict[str, Any]]) -> None:
        self._responses = responses
        self.calls: list[StructuredCall] = []

    def structured(self, call: StructuredCall) -> dict[str, Any]:
        self.calls.append(call)
        if call.kind == "critique":
            return {"problem_found": False, "explanation": None}
        stem = Path(call.document_id).stem
        return self._responses[stem]


def _scoped(deps: Deps, provider: Any) -> Deps:
    return Deps(
        provider=provider,
        catalogue=deps.catalogue,
        payment=deps.payment,
        clock=deps.clock,
        registry=deps.registry,
    )


def test_a_directory_of_documents_is_processed_and_summarised(
    tmp_path: Path, deps: Deps
) -> None:
    (tmp_path / "invoice_a.txt").write_text("irrelevant", encoding="utf-8")
    (tmp_path / "invoice_b.txt").write_text("irrelevant", encoding="utf-8")
    provider = _DirectoryScriptedProvider(
        {
            "invoice_a": {
                "vendor": {"name": "Widgets Inc."},
                "line_items": [{"item": "WidgetA", "quantity": 1, "unit_price": "250.00"}],
                "total": "250.00",
            },
            "invoice_b": {
                "vendor": {"name": "Widgets Inc."},
                "line_items": [{"item": "WidgetA", "quantity": 1, "unit_price": "250.00"}],
                "total": "15000.00",
            },
        }
    )
    scoped = _scoped(deps, provider)

    summary = run_batch(tmp_path, scoped)

    assert len(summary.items) == 2
    assert summary.counts == {"approved": 1, "escalated": 1}


def test_a_document_that_fails_extraction_does_not_stop_the_rest_of_the_batch(
    tmp_path: Path, deps: Deps
) -> None:
    (tmp_path / "invoice_bad.txt").write_text("irrelevant", encoding="utf-8")
    (tmp_path / "invoice_good.txt").write_text("irrelevant", encoding="utf-8")
    # No recording at all for "invoice_bad" — its extraction call raises KeyError from
    # the scripted provider's own dict lookup, standing in for any real extraction
    # failure a batch has no way to predict in advance.
    provider = _DirectoryScriptedProvider(
        {
            "invoice_good": {
                "vendor": {"name": "Widgets Inc."},
                "line_items": [{"item": "WidgetA", "quantity": 1, "unit_price": "250.00"}],
                "total": "250.00",
            },
        }
    )
    scoped = _scoped(deps, provider)

    summary = run_batch(tmp_path, scoped)

    assert len(summary.items) == 2
    by_name = {item.document_name: item for item in summary.items}
    assert by_name["invoice_bad.txt"].outcome == "failed"
    assert by_name["invoice_bad.txt"].error is not None
    assert by_name["invoice_good.txt"].outcome == "approved"


def test_an_unsupported_file_in_the_directory_is_silently_skipped(
    tmp_path: Path, deps: Deps
) -> None:
    """A stray README or .docx sitting in an invoice inbox is not a batch failure —
    it was never a document this system reads in the first place."""
    (tmp_path / "README.md").write_text("not an invoice", encoding="utf-8")
    (tmp_path / "invoice_good.txt").write_text("irrelevant", encoding="utf-8")
    provider = _DirectoryScriptedProvider(
        {
            "invoice_good": {
                "vendor": {"name": "Widgets Inc."},
                "line_items": [{"item": "WidgetA", "quantity": 1, "unit_price": "250.00"}],
                "total": "250.00",
            },
        }
    )
    scoped = _scoped(deps, provider)

    summary = run_batch(tmp_path, scoped)

    assert len(summary.items) == 1
    assert summary.items[0].document_name == "invoice_good.txt"


def test_processing_the_same_directory_twice_pays_the_same_count_not_double(
    tmp_path: Path, deps: Deps
) -> None:
    (tmp_path / "invoice_a.txt").write_text("irrelevant", encoding="utf-8")
    provider = _DirectoryScriptedProvider(
        {
            "invoice_a": {
                "invoice_number": "9001",
                "vendor": {"name": "Widgets Inc."},
                "line_items": [{"item": "WidgetA", "quantity": 1, "unit_price": "250.00"}],
                "total": "250.00",
            },
        }
    )
    scoped = _scoped(deps, provider)

    first = run_batch(tmp_path, scoped)
    second = run_batch(tmp_path, scoped)

    assert first.counts == {"approved": 1}
    assert second.counts == {"approved": 1}  # decision is unaffected by prior payment
    assert len(deps.payment.payments) == 1  # type: ignore[attr-defined]  # not paid twice
