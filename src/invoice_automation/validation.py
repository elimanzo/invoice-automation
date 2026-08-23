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

from .deps import Deps
from .models import Flag, FlagSeverity, Invoice


def validate_invoice(invoice: Invoice, deps: Deps) -> list[Flag]:
    """Check an invoice against the catalogue. Returns every flag raised."""
    flags: list[Flag] = []

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
