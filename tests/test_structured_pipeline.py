"""Structured formats through the primary seam: no provider call, fatal flags still bite.

`deps` (from conftest) uses a FakeProvider with no recording for these documents — if the
pipeline ever fell back to the model path for them, these tests would fail with
MissingRecording, not silently pass. That absence of a recording is what makes "zero
provider calls" verifiable rather than assumed.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from invoice_automation.deps import Deps
from invoice_automation.documents import load_document
from invoice_automation.graph import run_invoice
from invoice_automation.providers import FakeProvider
from invoice_automation.structured_parsing import cross_check


def test_a_structured_invoice_processes_with_zero_provider_calls(
    invoices_dir: Path, deps: Deps
) -> None:
    document = load_document(invoices_dir / "invoice_1004.json")
    assert isinstance(deps.provider, FakeProvider)

    result = run_invoice(document, deps)

    assert result.extraction_method == "deterministic"
    assert result.invoice is not None
    assert result.invoice.vendor.name == "Precision Parts Ltd."
    assert deps.provider.calls == []


def test_negative_quantity_and_empty_vendor_parse_cleanly_but_are_rejected(
    invoices_dir: Path, deps: Deps
) -> None:
    """invoice_1009.json: no exception during ingestion, and no payment at the end."""
    document = load_document(invoices_dir / "invoice_1009.json")

    result = run_invoice(document, deps)

    assert result.extraction_method == "deterministic"
    assert result.invoice is not None
    assert result.invoice.vendor.name == ""
    assert result.invoice.line_items[0].quantity == -5

    assert any(flag.code == "negative_quantity" for flag in result.flags)
    assert result.decision is not None
    assert result.decision.outcome == "rejected"
    assert result.payment is None
    assert deps.payment.payments == []  # type: ignore[attr-defined]  # never called


def test_a_document_the_deterministic_parser_cannot_read_falls_back_to_the_model(
    tmp_path: Path, deps: Deps
) -> None:
    """A JSON file that parses as JSON but not as an invoice: the graph must not fail
    outright, it must fall back to model extraction — which here has a recording."""
    from invoice_automation.providers import FakeProvider as FP

    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    document_path = tmp_path / "invoice_weird.json"
    document_path.write_text('{"totally": "not an invoice shape"}', encoding="utf-8")
    (responses_dir / "invoice_weird.json").write_text(
        '{"vendor": {"name": "Recovered By Model"}, "line_items": [], "total": "0.00"}',
        encoding="utf-8",
    )
    scoped_deps = Deps(
        provider=FP.with_sample_responses(responses_dir),
        catalogue=deps.catalogue,
        payment=deps.payment,
        clock=deps.clock,
        registry=deps.registry,
    )

    result = run_invoice(load_document(document_path), scoped_deps)

    assert result.extraction_method == "model"
    assert result.invoice is not None
    assert result.invoice.vendor.name == "Recovered By Model"


class TestCrossCheck:
    def test_agreement_reports_no_flags(self, invoices_dir: Path, deps: Deps) -> None:
        document = load_document(invoices_dir / "invoice_1004.json")
        model_deps = Deps(
            provider=FakeProvider(
                responses={
                    "invoice_1004": {
                        "vendor": {"name": "Precision Parts Ltd."},
                        "invoice_number": "INV-1004",
                        "invoice_date": "2026-01-22",
                        "due_date": "2026-02-22",
                        "total": "1890.00",
                        "currency": "USD",
                        "line_items": [
                            {"item": "WidgetA", "quantity": 3, "unit_price": "250.00"},
                            {"item": "WidgetB", "quantity": 2, "unit_price": "500.00"},
                        ],
                    }
                }
            ),
            catalogue=deps.catalogue,
            payment=deps.payment,
            clock=deps.clock,
            registry=deps.registry,
        )

        flags = cross_check(document, model_deps)

        assert flags == []

    def test_disagreement_is_flagged_per_field(self, invoices_dir: Path, deps: Deps) -> None:
        document = load_document(invoices_dir / "invoice_1004.json")
        model_deps = Deps(
            provider=FakeProvider(
                responses={
                    "invoice_1004": {
                        "vendor": {"name": "Precision Parts Ltd."},
                        # Wrong total and wrong line-item count on purpose.
                        "total": "1990.00",
                        "line_items": [
                            {"item": "WidgetA", "quantity": 3, "unit_price": "250.00"},
                        ],
                    }
                }
            ),
            catalogue=deps.catalogue,
            payment=deps.payment,
            clock=deps.clock,
            registry=deps.registry,
        )

        flags = cross_check(document, model_deps)

        codes_and_messages = [flag.message for flag in flags]
        assert any("total" in msg for msg in codes_and_messages)
        assert any("line item count" in msg for msg in codes_and_messages)
        assert all(flag.code == "extraction_disagreement" for flag in flags)

    def test_one_document_of_each_structured_format_can_be_cross_checked(
        self, invoices_dir: Path, deps: Deps
    ) -> None:
        """Not asserting agreement here, just that the mechanism runs end to end for
        every structured format without raising."""
        for name, response in (
            (
                "invoice_1004.json",
                {"vendor": {"name": "Precision Parts Ltd."}, "line_items": []},
            ),
            (
                "invoice_1006.csv",
                {"vendor": {"name": "Acme Industrial Supplies"}, "line_items": []},
            ),
            (
                "invoice_1014.xml",
                {"vendor": {"name": "TechParts International"}, "line_items": []},
            ),
        ):
            document = load_document(invoices_dir / name)
            model_deps = Deps(
                provider=FakeProvider(responses={document.path.stem: response}),
                catalogue=deps.catalogue,
                payment=deps.payment,
                clock=deps.clock,
                registry=deps.registry,
            )
            flags = cross_check(document, model_deps)
            assert isinstance(flags, list)
