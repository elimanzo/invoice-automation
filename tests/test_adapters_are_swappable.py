"""Acme-side systems are adapters, not stubs.

If these pass with hand-written in-test implementations, then swapping SQLite for a
real ERP or the mock payment for a banking API is a construction change at the edge
and nothing else.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from invoice_automation.catalogue import CatalogueItem
from invoice_automation.deps import Deps
from invoice_automation.documents import load_document
from invoice_automation.extraction import extract_invoice
from invoice_automation.payments import PaymentResult
from invoice_automation.providers import FakeProvider


class InMemoryCatalogue:
    """What an ERP adapter would look like: any source, same interface."""

    def __init__(self, items: dict[str, CatalogueItem], vendors: set[str]) -> None:
        self._items = items
        self._vendors = vendors

    def get_item(self, name: str) -> CatalogueItem | None:
        return self._items.get(name)

    def all_items(self) -> list[CatalogueItem]:
        return list(self._items.values())

    def is_known_vendor(self, name: str) -> bool:
        return name in self._vendors


class RefusingPayment:
    """A payment adapter that declines, as a real one sometimes does."""

    def pay(self, vendor: str, amount: Decimal, currency: str = "USD") -> PaymentResult:
        return PaymentResult(status="declined", reference=None, detail="insufficient funds")


class NullRegistry:
    def payment_recorded(self, invoice_number: str) -> bool:
        return False

    def record_payment(self, invoice_number: str, vendor: str, amount: Decimal) -> None:
        pass


class FrozenClock:
    def today(self) -> date:
        return date(2026, 2, 1)


def test_pipeline_dependencies_accept_foreign_implementations(invoices_dir: Path) -> None:
    deps = Deps(
        provider=FakeProvider.with_sample_responses(),
        catalogue=InMemoryCatalogue(
            items={"WidgetA": CatalogueItem("WidgetA", 99, Decimal("1.00"))},
            vendors={"Some Other Vendor"},
        ),
        payment=RefusingPayment(),
        clock=FrozenClock(),
        registry=NullRegistry(),
    )

    invoice = extract_invoice(load_document(invoices_dir / "invoice_1001.txt"), deps)

    assert invoice.invoice_number == "INV-1001"
    widget_a = deps.catalogue.get_item("WidgetA")
    assert widget_a is not None and widget_a.stock == 99
    assert deps.payment.pay("Widgets Inc.", Decimal("1.00")).status == "declined"
