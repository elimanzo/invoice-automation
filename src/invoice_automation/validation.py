"""Validation: an invoice against the catalogue.

This is still the thin tracer-bullet version. Item matching, unknown items, vendor
checks, pricing, currency, and risk scoring all arrive with tickets 06 and 07.

Stock is checked per line item, not aggregated across an invoice's lines yet: aggregation
(what ticket 07 needs to catch INV-1013's WidgetA totalling 22 against 15 in stock) would
require a policy this ticket does not yet have reason to build.

Negative quantity is checked here, ahead of ticket 07's fuller rule set, because ticket 05
needs it: a structured document can carry a negative quantity and parse without error
(the brief's own sample data does — a malformed number is not a parse failure, it is a
value that is simply wrong), and proving that "parses cleanly" and "gets rejected" are
different things requires a rule to reject it with.
"""

from __future__ import annotations

from decimal import Decimal

from .deps import Deps
from .models import Flag, FlagSeverity, Invoice

_ROUNDING_TOLERANCE = Decimal("0.01")


def validate_invoice(invoice: Invoice, deps: Deps) -> list[Flag]:
    """Check an invoice against the catalogue. Returns every flag raised."""
    flags: list[Flag] = []

    if _line_items_disagree_with_stated_total(invoice):
        stated_field = "subtotal" if invoice.subtotal is not None else "total"
        stated_value = invoice.subtotal if invoice.subtotal is not None else invoice.total
        flags.append(
            Flag(
                severity=FlagSeverity.SOFT,
                code="total_mismatch",
                message=(
                    f"stated {stated_field} {stated_value} does not match the sum of "
                    f"line items {invoice.line_items_total}"
                ),
            )
        )

    for line in invoice.line_items:
        if line.quantity < 0:
            flags.append(
                Flag(
                    severity=FlagSeverity.FATAL,
                    code="negative_quantity",
                    message=f"{line.item}: quantity {line.quantity} is negative",
                )
            )
            continue  # A stock comparison against a negative quantity says nothing useful.

        catalogue_item = deps.catalogue.get_item(line.item)
        if catalogue_item is None:
            continue  # Unknown-item handling arrives with ticket 07.
        if line.quantity > catalogue_item.stock:
            flags.append(
                Flag(
                    severity=FlagSeverity.FATAL,
                    code="stock_exceeded",
                    message=(
                        f"{line.item}: invoice requests {line.quantity}, "
                        f"only {catalogue_item.stock} in stock"
                    ),
                )
            )

    return flags


def _line_items_disagree_with_stated_total(invoice: Invoice) -> bool:
    """Whether the line items don't add up to what the invoice says they should.

    Compared against subtotal when it's stated — subtotal is defined as the pre-tax sum
    of line items, so it's the number that should always match regardless of tax rate or
    other charges. Falling back to total only when there's no tax to account for avoids
    a false positive on every taxed invoice: total includes tax (and sometimes shipping,
    which nothing in this model tracks), so total alone disagreeing with the line items
    is expected, not a data problem.
    """
    if invoice.line_items_total is None:
        return False  # An unpriced line means there's nothing reliable to compare yet.

    if invoice.subtotal is not None:
        return abs(invoice.subtotal - invoice.line_items_total) > _ROUNDING_TOLERANCE

    if invoice.total is not None and not invoice.tax_amount:
        return abs(invoice.total - invoice.line_items_total) > _ROUNDING_TOLERANCE

    return False
