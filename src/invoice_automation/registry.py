"""The registry: what has already been paid, and what has already been seen.

An invoice is one obligation and may be paid at most once, however many documents carry
it. The registry is what makes that enforceable — and enforced at the storage layer, by
a uniqueness constraint on invoice identity, rather than by application code remembering
to check.

Payments and seen invoices are two different things kept here on purpose. A payment
exists only after an invoice is approved and paid. A seen-invoice snapshot exists the
moment any document for an identity is first extracted — reconciliation (ticket 10) must
catch a duplicate arriving for an invoice that was rejected or escalated, not paid, just
as much as one that was.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

_INVOICE_NUMBER = re.compile(r"(?:INV[-\s#]*)?(\d+)", re.IGNORECASE)


def normalize_invoice_identity(invoice_number: str | None) -> str | None:
    """The canonical identity a payment is keyed on.

    "INV-1002" and a bare "1002" (invoice_1002.txt states it without the usual prefix)
    must be the same identity, or formatting alone would defeat idempotency. Keeping
    just the digit sequence is what makes that true. Vendor as a tiebreak when there's
    no number at all is ticket 10's job (reconciliation); here, no number means no
    identity to key on, and the caller decides what that means for payment.
    """
    if invoice_number is None or not invoice_number.strip():
        return None
    match = _INVOICE_NUMBER.search(invoice_number)
    return match.group(1) if match is not None else invoice_number.strip()


class DuplicatePayment(Exception):
    """Raised when a second payment is attempted for one invoice identity."""


@dataclass(frozen=True)
class PaymentRecord:
    invoice_number: str
    vendor: str
    amount: Decimal


@dataclass(frozen=True)
class SeenInvoice:
    """A snapshot of the most recently reconciled invoice for one identity.

    Not a payment record and not the raw document — the JSON-shaped `Invoice` that
    resulted from reconciling every document seen for this identity so far, which is
    exactly what the next document arriving for the same identity needs to compare
    itself against.
    """

    identity: str
    document_name: str
    invoice: dict[str, Any]


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

    def get_seen_invoice(self, identity: str) -> SeenInvoice | None:
        """What reconciliation last recorded for this identity, if anything."""
        ...

    def record_seen_invoice(self, identity: str, document_name: str, invoice: dict[str, Any]) -> None:
        """Replace what's recorded for this identity — reconciliation calls this after
        deciding what the current true state for the identity is (the first document
        seen, or a merge, or a revision superseding what came before)."""
        ...


_SCHEMA = """
CREATE TABLE IF NOT EXISTS payments (
    invoice_number TEXT PRIMARY KEY,
    vendor         TEXT NOT NULL,
    amount         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS seen_invoices (
    identity      TEXT PRIMARY KEY,
    document_name TEXT NOT NULL,
    invoice_json  TEXT NOT NULL
);
"""


class SqliteRegistry:
    """SQLite-backed registry.

    `invoice_number` is the primary key on payments, so a duplicate payment is rejected
    by the database itself. A code path that forgot to check first still cannot pay
    twice.
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

    def get_seen_invoice(self, identity: str) -> SeenInvoice | None:
        with closing(sqlite3.connect(self._path)) as conn:
            row = conn.execute(
                "SELECT identity, document_name, invoice_json FROM seen_invoices WHERE identity = ?",
                (identity,),
            ).fetchone()
        if row is None:
            return None
        identity_value, document_name, invoice_json = row
        return SeenInvoice(
            identity=identity_value, document_name=document_name, invoice=json.loads(invoice_json)
        )

    def record_seen_invoice(
        self, identity: str, document_name: str, invoice: dict[str, Any]
    ) -> None:
        with closing(sqlite3.connect(self._path)) as conn, conn:
            conn.execute(
                "INSERT INTO seen_invoices (identity, document_name, invoice_json) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(identity) DO UPDATE SET "
                "document_name = excluded.document_name, invoice_json = excluded.invoice_json",
                (identity, document_name, json.dumps(invoice)),
            )
