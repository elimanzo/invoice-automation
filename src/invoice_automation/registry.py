"""The registry: what has already been paid.

An invoice is one obligation and may be paid at most once, however many documents carry
it. The registry is what makes that enforceable — and enforced at the storage layer, by
a uniqueness constraint on invoice identity, rather than by application code remembering
to check.

Reconciliation of conflicting documents and superseding revisions is a later ticket.
What exists here is the identity and payment record those depend on.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Protocol, runtime_checkable


class DuplicatePayment(Exception):
    """Raised when a second payment is attempted for one invoice identity."""


@dataclass(frozen=True)
class PaymentRecord:
    invoice_number: str
    vendor: str
    amount: Decimal


@runtime_checkable
class Registry(Protocol):
    def payment_recorded(self, invoice_number: str) -> bool:
        """Whether this invoice identity has already been paid."""
        ...

    def record_payment(self, invoice_number: str, vendor: str, amount: Decimal) -> None:
        """Record a payment. Raises DuplicatePayment for an identity already paid."""
        ...

    def payments(self) -> list[PaymentRecord]:
        """Every payment made. Read-only history — ticket 08's approval agent uses
        this to build a vendor's prior-payment record; nothing in the pipeline writes
        through it."""
        ...


_SCHEMA = """
CREATE TABLE IF NOT EXISTS payments (
    invoice_number TEXT PRIMARY KEY,
    vendor         TEXT NOT NULL,
    amount         TEXT NOT NULL
);
"""


class SqliteRegistry:
    """SQLite-backed registry.

    `invoice_number` is the primary key, so a duplicate payment is rejected by the
    database itself. A code path that forgot to check first still cannot pay twice.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.executescript(_SCHEMA)

    def payment_recorded(self, invoice_number: str) -> bool:
        with closing(sqlite3.connect(self._path)) as conn:
            row = conn.execute(
                "SELECT 1 FROM payments WHERE invoice_number = ?", (invoice_number,)
            ).fetchone()
        return row is not None

    def record_payment(self, invoice_number: str, vendor: str, amount: Decimal) -> None:
        try:
            with closing(sqlite3.connect(self._path)) as conn, conn:
                conn.execute(
                    "INSERT INTO payments (invoice_number, vendor, amount) VALUES (?, ?, ?)",
                    (invoice_number, vendor, str(amount)),
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicatePayment(
                f"{invoice_number} has already been paid; refusing a second payment"
            ) from exc

    def payments(self) -> list[PaymentRecord]:
        with closing(sqlite3.connect(self._path)) as conn:
            rows = conn.execute(
                "SELECT invoice_number, vendor, amount FROM payments ORDER BY invoice_number"
            ).fetchall()
        return [PaymentRecord(number, vendor, Decimal(amount)) for number, vendor, amount in rows]
