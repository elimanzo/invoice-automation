"""Deterministic repair: unit-level, no provider involved."""

from __future__ import annotations

from pathlib import Path

from invoice_automation.repair import (
    find_purchase_order_reference,
    find_unresolvable_due_date,
    repair_ocr_digits,
)


class TestOcrDigitRepair:
    def test_fixes_a_corrupted_date_and_records_a_high_confidence_correction(self) -> None:
        result = repair_ocr_digits("DATE: 26-Jan-2O26")

        assert result.text == "DATE: 26-Jan-2026"
        assert len(result.corrections) == 1
        correction = result.corrections[0]
        assert correction.raw == "26-Jan-2O26"
        assert correction.value == "26-Jan-2026"
        assert correction.confidence == 0.95

    def test_fixes_a_corrupted_amount(self) -> None:
        result = repair_ocr_digits("TOTAL: $3,500.O0")

        assert result.text == "TOTAL: $3,500.00"
        assert result.corrections[0].raw == "$3,500.O0"
        assert result.corrections[0].value == "$3,500.00"

    def test_fixes_both_corruptions_in_the_real_sample_document(self, invoices_dir: Path) -> None:
        raw_text = (invoices_dir / "invoice_1012.txt").read_text(encoding="utf-8")

        result = repair_ocr_digits(raw_text)

        assert "26-Jan-2026" in result.text
        assert "$3,500.00" in result.text
        assert len(result.corrections) == 2

    def test_leaves_ordinary_text_containing_o_untouched(self) -> None:
        result = repair_ocr_digits("Vendor: Good Widgets Co. Notes: On order.")

        assert result.text == "Vendor: Good Widgets Co. Notes: On order."
        assert result.corrections == []

    def test_a_substitution_that_produces_an_implausible_date_is_not_applied(self) -> None:
        """Day 32 doesn't exist even after fixing the year — don't guess, don't apply."""
        result = repair_ocr_digits("DATE: 32-Jan-2O26")

        assert result.text == "DATE: 32-Jan-2O26"  # unchanged
        assert len(result.corrections) == 1
        assert result.corrections[0].raw == result.corrections[0].value  # not repaired
        assert result.corrections[0].confidence < 0.5


class TestUnresolvableDueDate:
    def test_finds_a_relative_due_date(self, invoices_dir: Path) -> None:
        raw_text = (invoices_dir / "invoice_1003.txt").read_text(encoding="utf-8")

        assert find_unresolvable_due_date(raw_text) == "yesterday"

    def test_a_real_calendar_date_is_not_flagged(self, invoices_dir: Path) -> None:
        raw_text = (invoices_dir / "invoice_1001.txt").read_text(encoding="utf-8")

        assert find_unresolvable_due_date(raw_text) is None

    def test_no_due_date_line_at_all_is_not_flagged(self) -> None:
        assert find_unresolvable_due_date("Vendor: X\nTotal: $5.00") is None


class TestPurchaseOrderReference:
    def test_finds_a_referenced_po(self, invoices_dir: Path) -> None:
        raw_text = (invoices_dir / "invoice_1012.txt").read_text(encoding="utf-8")

        assert find_purchase_order_reference(raw_text) == "PO-20260115"

    def test_no_reference_present_returns_none(self, invoices_dir: Path) -> None:
        raw_text = (invoices_dir / "invoice_1001.txt").read_text(encoding="utf-8")

        assert find_purchase_order_reference(raw_text) is None
