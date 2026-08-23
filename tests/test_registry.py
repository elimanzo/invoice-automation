"""The registry: invoice identity, and the storage-layer guarantee against double
payment. No dedicated tests existed for this before ticket 09, even though the
mechanism (a PRIMARY KEY on invoice_number) has existed since ticket 01."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from invoice_automation.registry import (
    DuplicatePayment,
    SqliteRegistry,
    normalize_invoice_identity,
)


class TestIdentityNormalization:
    def test_a_prefixed_and_a_bare_number_are_the_same_identity(self) -> None:
        assert normalize_invoice_identity("INV-1002") == normalize_invoice_identity("1002")

    def test_the_common_prefix_formats_all_normalise(self) -> None:
        assert normalize_invoice_identity("INV-1001") == "1001"
        assert normalize_invoice_identity("INV 1012") == "1012"
        assert normalize_invoice_identity("INV1013") == "1013"
        assert normalize_invoice_identity("1002") == "1002"

    def test_invoice_1002_specifically_resolves_to_the_expected_identity(self) -> None:
        """invoice_1002.txt states its number as bare '1002', with no 'INV-' prefix."""
        assert normalize_invoice_identity("1002") == "1002"
        assert normalize_invoice_identity("1002") == normalize_invoice_identity("INV-1002")

    def test_no_number_at_all_has_no_identity(self) -> None:
        assert normalize_invoice_identity(None) is None
        assert normalize_invoice_identity("") is None
        assert normalize_invoice_identity("   ") is None


class TestSqliteRegistry:
    def test_a_payment_is_recorded_and_then_reported_as_recorded(self, tmp_path: Path) -> None:
        registry = SqliteRegistry(tmp_path / "registry.db")

        assert registry.payment_recorded("1001") is False
        registry.record_payment("1001", "Widgets Inc.", Decimal("5000.00"))
        assert registry.payment_recorded("1001") is True

    def test_a_second_write_for_the_same_identity_is_rejected_by_the_database(
        self, tmp_path: Path
    ) -> None:
        """The UNIQUE constraint (a PRIMARY KEY on invoice_number) does this at the
        storage layer — a bug that skipped the payment_recorded() check in application
        code still cannot pay the same identity twice."""
        registry = SqliteRegistry(tmp_path / "registry.db")
        registry.record_payment("1001", "Widgets Inc.", Decimal("5000.00"))

        with pytest.raises(DuplicatePayment):
            registry.record_payment("1001", "Widgets Inc.", Decimal("5000.00"))

    def test_payments_lists_everything_recorded(self, tmp_path: Path) -> None:
        registry = SqliteRegistry(tmp_path / "registry.db")
        registry.record_payment("1001", "Widgets Inc.", Decimal("5000.00"))
        registry.record_payment("1002", "Gadgets Co.", Decimal("15000.00"))

        payments = registry.payments()

        assert {(p.invoice_number, p.vendor, p.amount) for p in payments} == {
            ("1001", "Widgets Inc.", Decimal("5000.00")),
            ("1002", "Gadgets Co.", Decimal("15000.00")),
        }
