"""The invoice domain model.

Vocabulary follows CONTEXT.md exactly. An **invoice** is one obligation, identified by
its invoice number, payable at most once. A **document** is one file that carries an
invoice; several documents may carry the same invoice and disagree. A **line item** is
one row of an invoice, and line items for the same item are never merged.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field


class DocumentFormat(StrEnum):
    TEXT = "text"
    JSON = "json"
    CSV = "csv"
    XML = "xml"
    PDF = "pdf"


class Vendor(BaseModel):
    """The party requesting payment."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Vendor name as stated on the document")
    address: str | None = Field(default=None)


class LineItem(BaseModel):
    """One row of an invoice: an item, how many, at what price.

    The same item may appear on several line items at different prices — a volume
    discount, a rush premium, a replacement. Merging them would destroy the
    distinction, so nothing here does.
    """

    model_config = ConfigDict(frozen=True)

    item: str = Field(description="Item name exactly as the document states it")
    quantity: int = Field(description="Units billed; may be negative on a malformed document")
    unit_price: Decimal | None = Field(default=None)
    stated_amount: Decimal | None = Field(
        default=None,
        description="Line total as stated on the document, when it states one",
    )
    note: str | None = Field(
        default=None,
        description="Annotation the document attaches to this line, e.g. a discount reason",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def amount(self) -> Decimal | None:
        """The line total.

        Prefers what the document stated. Falls back to quantity times unit price —
        arithmetic, not a correction, so it records nothing.
        """
        if self.stated_amount is not None:
            return self.stated_amount
        if self.unit_price is None:
            return None
        return self.unit_price * self.quantity


class Invoice(BaseModel):
    """One payment obligation from a vendor."""

    model_config = ConfigDict(frozen=True)

    invoice_number: str | None = Field(
        default=None,
        description="Identifier as stated; may lack the usual prefix or be absent entirely",
    )
    vendor: Vendor
    invoice_date: date | None = Field(default=None)
    due_date: date | None = Field(default=None)
    line_items: list[LineItem] = Field(default_factory=list)
    subtotal: Decimal | None = Field(default=None)
    tax_amount: Decimal | None = Field(default=None)
    total: Decimal | None = Field(default=None)
    currency: str = Field(default="USD", description="ISO code; USD when the document is silent")
    payment_terms: str | None = Field(default=None)
    purchase_order_reference: str | None = Field(
        default=None,
        description=(
            "Purchase order cited by the document, when it cites one. Captured but not "
            "matched: no purchase-order records exist to match against."
        ),
    )
    notes: str | None = Field(default=None)
    revision: str | None = Field(
        default=None,
        description=(
            "A document's own declaration that it supersedes an earlier one for the "
            "same invoice number (e.g. invoice_1004_revised.json's \"revision\": "
            "\"R1\"). Absent for an ordinary invoice; ticket 10's reconciliation is "
            "the only reader of this field."
        ),
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def unpriced_line_count(self) -> int:
        """Line items whose amount cannot be determined at all."""
        return sum(1 for item in self.line_items if item.amount is None)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def line_items_total(self) -> Decimal | None:
        """Sum of the line items, for comparison against the stated total.

        None when any line amount is unknown. Treating an unknown line as zero would
        make the sum quietly *smaller*, so an invoice with a missing amount could match
        its stated total and pass a check it should have failed.
        """
        if self.unpriced_line_count:
            return None
        return sum((item.amount or Decimal(0) for item in self.line_items), Decimal(0))


class FlagSeverity(StrEnum):
    """How a flag affects control flow.

    fatal rejects the invoice and it never reaches payment. soft does not block, but
    contributes to the risk score. info is recorded only. See CONTEXT.md.
    """

    FATAL = "fatal"
    SOFT = "soft"
    INFO = "info"


class Flag(BaseModel):
    """A finding about an invoice, raised by validation or approval."""

    model_config = ConfigDict(frozen=True)

    severity: FlagSeverity
    code: str = Field(description="Short machine-stable identifier, e.g. 'stock_exceeded'")
    message: str = Field(description="Human-readable explanation, naming the specifics")


class Correction(BaseModel):
    """A record that the system stored something different from what a document said.

    Written for every mutation, always — an audit trail, not an exception path. See
    CONTEXT.md. Repair itself arrives with ticket 06; this model exists now so the
    primary seam's return shape does not change shape later.
    """

    model_config = ConfigDict(frozen=True)

    field: str
    raw: str
    value: str
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)


class Decision(BaseModel):
    """The outcome of approval."""

    model_config = ConfigDict(frozen=True)

    outcome: Literal["approved", "rejected", "escalated"]
    reasoning: str


class ToolCallRecord(BaseModel):
    """One read-only tool call the approval agent made, and what it got back — the
    trace ticket 08 asks for, so an investigation can be inspected after the fact."""

    model_config = ConfigDict(frozen=True)

    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]
