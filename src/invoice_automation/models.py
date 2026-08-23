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
