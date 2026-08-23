"""Payment: the mocked banking API, behind an interface.

The brief supplies `mock_payment(vendor, amount)`. That is one implementation of a
`PaymentGateway`; a real banking API would be another, and no pipeline stage would
change. Payment is the last thing that happens and the only irreversible one, which is
why nothing here decides anything — it is handed a decision already made.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal, Protocol, runtime_checkable


@dataclass(frozen=True)
class PaymentResult:
    status: Literal["success", "declined", "error"]
    reference: str | None = None
    detail: str | None = None


@runtime_checkable
class PaymentGateway(Protocol):
    def pay(self, vendor: str, amount: Decimal, currency: str = "USD") -> PaymentResult:
        """Transfer funds to a vendor. Called only for an approved invoice."""
        ...


class MockPayment:
    """The brief's mock payment function, as an adapter.

    Prints and succeeds, exactly as specified. Nothing moves.
    """

    def pay(self, vendor: str, amount: Decimal, currency: str = "USD") -> PaymentResult:
        print(f"Paid {amount} {currency} to {vendor}")
        return PaymentResult(status="success", reference=f"MOCK-{vendor[:4].upper()}")


@dataclass
class RecordingPayment:
    """Records payments instead of printing them, so tests can assert on them."""

    payments: list[tuple[str, Decimal, str]] = field(default_factory=list)

    def pay(self, vendor: str, amount: Decimal, currency: str = "USD") -> PaymentResult:
        self.payments.append((vendor, amount, currency))
        return PaymentResult(status="success", reference=f"TEST-{len(self.payments)}")
