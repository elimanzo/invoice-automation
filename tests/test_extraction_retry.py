"""The self-correction loop: a schema violation is fed back and retried.

`FakeProvider` always returns the same recording regardless of feedback, so it can't
exercise this — a small scripted double that returns a different payload per call is
what actually tests the retry, not the fixture used everywhere else.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from invoice_automation.deps import Deps
from invoice_automation.documents import load_document
from invoice_automation.extraction import ExtractionFailed, extract_invoice
from invoice_automation.providers import StructuredCall


class _ScriptedProvider:
    """Returns one payload per call, in order. Records what it was asked."""

    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self._payloads = list(payloads)
        self.calls: list[StructuredCall] = []

    def structured(self, call: StructuredCall) -> dict[str, Any]:
        self.calls.append(call)
        return self._payloads.pop(0)


def _document(tmp_path: Path) -> Any:
    path = tmp_path / "invoice_1001.txt"
    path.write_text("irrelevant text; the scripted provider ignores it", encoding="utf-8")
    return load_document(path)


def test_a_non_numeric_quantity_triggers_a_retry_that_then_succeeds(
    tmp_path: Path, deps: Deps
) -> None:
    bad = {
        "vendor": {"name": "Widgets Inc."},
        "line_items": [{"item": "WidgetA", "quantity": "five", "unit_price": "250.00"}],
    }
    good = {
        "vendor": {"name": "Widgets Inc."},
        "line_items": [{"item": "WidgetA", "quantity": 5, "unit_price": "250.00"}],
    }
    provider = _ScriptedProvider([bad, good])
    scoped_deps = Deps(
        provider=provider,
        catalogue=deps.catalogue,
        payment=deps.payment,
        clock=deps.clock,
        registry=deps.registry,
    )

    invoice = extract_invoice(_document(tmp_path), scoped_deps)

    assert invoice.line_items[0].quantity == 5
    assert len(provider.calls) == 2
    # The retry must actually carry feedback, not just repeat the same prompt.
    assert provider.calls[1].user != provider.calls[0].user
    assert "REJECTED" in provider.calls[1].user


def test_exhausting_the_retry_cap_raises_extraction_failed(tmp_path: Path, deps: Deps) -> None:
    always_bad = {"line_items": [{"item": "WidgetA", "quantity": "still not a number"}]}
    provider = _ScriptedProvider([always_bad, always_bad, always_bad])
    scoped_deps = Deps(
        provider=provider,
        catalogue=deps.catalogue,
        payment=deps.payment,
        clock=deps.clock,
        registry=deps.registry,
    )

    with pytest.raises(ExtractionFailed):
        extract_invoice(_document(tmp_path), scoped_deps)

    # DEFAULT_EXTRACTION_MAX_ATTEMPTS is 2: it must not keep retrying forever.
    assert len(provider.calls) == 2


def test_the_clean_invoice_still_needs_exactly_one_call(invoices_dir: Path, deps: Deps) -> None:
    """The common case must not pay the cost of the retry machinery."""
    from invoice_automation.providers import FakeProvider

    assert isinstance(deps.provider, FakeProvider)
    extract_invoice(load_document(invoices_dir / "invoice_1001.txt"), deps)

    assert len(deps.provider.calls) == 1
