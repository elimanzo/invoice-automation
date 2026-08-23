"""Extraction: repair applied before the model sees the text, the critic reviewing
after, and the corrections/flags that come out of both."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from invoice_automation.deps import Deps
from invoice_automation.documents import load_document
from invoice_automation.extraction import extract_invoice
from invoice_automation.providers import StructuredCall


class _ScriptedProvider:
    """One extraction payload, and a scripted sequence of critique verdicts."""

    def __init__(
        self, extraction_payloads: list[dict[str, Any]], critiques: list[dict[str, Any]]
    ) -> None:
        self._extractions = list(extraction_payloads)
        self._critiques = list(critiques)
        self.calls: list[StructuredCall] = []

    def structured(self, call: StructuredCall) -> dict[str, Any]:
        self.calls.append(call)
        if call.kind == "critique":
            return self._critiques.pop(0)
        return self._extractions.pop(0)


def _document(tmp_path: Path, text: str = "irrelevant; the scripted provider ignores it") -> Any:
    path = tmp_path / "invoice_x.txt"
    path.write_text(text, encoding="utf-8")
    return load_document(path)


class TestRepairAppliedBeforeExtraction:
    def test_the_model_receives_the_repaired_text_not_the_raw_text(
        self, tmp_path: Path, deps: Deps
    ) -> None:
        provider = _ScriptedProvider(
            extraction_payloads=[{"vendor": {"name": "X"}, "line_items": []}],
            critiques=[{"problem_found": False}],
        )
        scoped = Deps(
            provider=provider,
            catalogue=deps.catalogue,
            payment=deps.payment,
            clock=deps.clock,
            registry=deps.registry,
        )
        document = _document(tmp_path, "DATE: 26-Jan-2O26\nTOTAL: $3,500.O0")

        result = extract_invoice(document, scoped)

        assert "26-Jan-2026" in provider.calls[0].user
        assert "2O26" not in provider.calls[0].user
        assert len(result.corrections) == 2

    def test_corrections_are_returned_even_when_nothing_needed_repair(
        self, invoices_dir: Path, deps: Deps
    ) -> None:
        result = extract_invoice(load_document(invoices_dir / "invoice_1001.txt"), deps)

        assert result.corrections == []


class TestUnresolvableDueDateFlag:
    def test_a_relative_due_date_produces_an_info_flag(self, tmp_path: Path, deps: Deps) -> None:
        provider = _ScriptedProvider(
            extraction_payloads=[{"vendor": {"name": "X"}, "line_items": []}],
            critiques=[{"problem_found": False}],
        )
        scoped = Deps(
            provider=provider,
            catalogue=deps.catalogue,
            payment=deps.payment,
            clock=deps.clock,
            registry=deps.registry,
        )
        document = _document(tmp_path, "Due Date: yesterday")

        result = extract_invoice(document, scoped)

        assert any(flag.code == "due_date_unresolvable" for flag in result.flags)

    def test_the_critic_prompt_does_not_ask_for_an_unresolvable_due_date_to_be_filled_in(
        self,
    ) -> None:
        """Found live: broadening the critic to catch any clearly-stated-but-dropped
        field (the ticket 03 temperature fix) made it also flag a *correctly* null
        due_date for invoice_1003's 'Due Date: yesterday' as a problem, triggering a
        retry loop that exhausted both attempts and failed extraction entirely on the
        fraud/injection test case. This is a guardrail-text regression test: we cannot
        unit-test a real model's compliance offline, but we can make sure the exception
        clause that fixes it isn't silently removed later."""
        from invoice_automation.extraction import CRITIC_SYSTEM_PROMPT

        assert "yesterday" in CRITIC_SYSTEM_PROMPT
        assert "due_date" in CRITIC_SYSTEM_PROMPT


class TestCriticRetriesOnAProblem:
    def test_a_critique_finding_a_problem_triggers_re_extraction(
        self, tmp_path: Path, deps: Deps
    ) -> None:
        first_attempt = {"vendor": {"name": "Wrong Vendor"}, "line_items": []}
        second_attempt = {"vendor": {"name": "Correct Vendor"}, "line_items": []}
        provider = _ScriptedProvider(
            extraction_payloads=[first_attempt, second_attempt],
            critiques=[
                {"problem_found": True, "explanation": "vendor name does not match the document"},
                {"problem_found": False},
            ],
        )
        scoped = Deps(
            provider=provider,
            catalogue=deps.catalogue,
            payment=deps.payment,
            clock=deps.clock,
            registry=deps.registry,
        )

        result = extract_invoice(_document(tmp_path), scoped)

        assert result.invoice.vendor.name == "Correct Vendor"
        assert len(provider.calls) == 4  # extract, critique(problem), extract, critique(clean)
        assert "vendor name does not match" in provider.calls[2].user


class TestLiteralNullStrings:
    def test_a_string_literal_null_is_treated_as_absent(
        self, tmp_path: Path, deps: Deps
    ) -> None:
        """Found live: told to leave a field null, the model sometimes writes the text
        "null" rather than omitting the key. currency must still default to USD."""
        provider = _ScriptedProvider(
            extraction_payloads=[
                {
                    "vendor": {"name": "X"},
                    "currency": "null",
                    "payment_terms": "NULL",
                    "line_items": [],
                }
            ],
            critiques=[{"problem_found": False}],
        )
        scoped = Deps(
            provider=provider,
            catalogue=deps.catalogue,
            payment=deps.payment,
            clock=deps.clock,
            registry=deps.registry,
        )

        result = extract_invoice(_document(tmp_path), scoped)

        assert result.invoice.currency == "USD"
        assert result.invoice.payment_terms is None


class TestPurchaseOrderReference:
    def test_backstops_a_po_reference_the_model_missed(self, tmp_path: Path, deps: Deps) -> None:
        provider = _ScriptedProvider(
            extraction_payloads=[{"vendor": {"name": "X"}, "line_items": []}],
            critiques=[{"problem_found": False}],
        )
        scoped = Deps(
            provider=provider,
            catalogue=deps.catalogue,
            payment=deps.payment,
            clock=deps.clock,
            registry=deps.registry,
        )
        document = _document(tmp_path, "NOTES: Ref PO-20260115. Deliver to dock B.")

        result = extract_invoice(document, scoped)

        assert result.invoice.purchase_order_reference == "PO-20260115"

    def test_does_not_override_a_reference_the_model_already_found(
        self, tmp_path: Path, deps: Deps
    ) -> None:
        provider = _ScriptedProvider(
            extraction_payloads=[
                {
                    "vendor": {"name": "X"},
                    "purchase_order_reference": "PO-999999",
                    "line_items": [],
                }
            ],
            critiques=[{"problem_found": False}],
        )
        scoped = Deps(
            provider=provider,
            catalogue=deps.catalogue,
            payment=deps.payment,
            clock=deps.clock,
            registry=deps.registry,
        )
        document = _document(tmp_path, "NOTES: Ref PO-20260115.")

        result = extract_invoice(document, scoped)

        assert result.invoice.purchase_order_reference == "PO-999999"
