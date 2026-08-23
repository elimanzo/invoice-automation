"""Validation: an invoice against the catalogue.

This is the thin tracer-bullet version. It checks one thing — does a line item ask for
more than Acme has on hand — because that is the rule ticket 02 needs to prove the graph
end to end. Item matching, unknown items, negative quantities, vendor checks, pricing,
currency, and risk scoring all arrive with tickets 06 and 07.

Stock is checked per line item, not aggregated across an invoice's lines yet: aggregation
(what ticket 07 needs to catch INV-1013's WidgetA totalling 22 against 15 in stock) would
require a policy this ticket does not yet have reason to build.
"""

from __future__ import annotations

from .deps import Deps
from .models import Flag, FlagSeverity, Invoice


def validate_invoice(invoice: Invoice, deps: Deps) -> list[Flag]:
    """Check an invoice against the catalogue. Returns every flag raised."""
    flags: list[Flag] = []

    for line in invoice.line_items:
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
