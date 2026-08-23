"""Validation: an invoice against the catalogue, the vendor master, and policy.

Item matching follows ADR-0007 exactly: normalise, then match exactly. A miss stays
unknown — fuzzy matching only names the nearest catalogue entry in the flag text, and
never influences whether something matched. Measured on the sample data (see the ADR):
normalisation resolves every legitimate spacing/casing variant at similarity 1.000, while
an uncatalogued item like "WidgetC" still scores 0.857 against a real one — close enough
that a conventional fuzzy threshold would wrongly match it and let it through.

Quantities aggregate across every line item that shares a normalised name before the
stock check, so an order split across several lines can't evade it — INV-1013 bills
WidgetA three times at three different prices; the check that matters is whether 22 units
fit in 15, not whether any single line does.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher

from .catalogue import CatalogueItem
from .config import FX_RATES_TO_USD, UnknownCurrency
from .deps import Deps
from .models import Correction, Flag, FlagSeverity, Invoice, LineItem

_ROUNDING_TOLERANCE = Decimal("0.01")

# Below this, a nearest-catalogue-entry suggestion isn't worth naming — it would just be
# noise in the flag text for an item that plainly isn't a near-miss of anything real.
_SUGGESTION_SIMILARITY_FLOOR = 0.6


@dataclass(frozen=True)
class ValidationResult:
    """Everything validation produces: findings, an audit trail for any currency
    conversion it performed, and the USD-equivalent total approval compares against a
    dollar threshold with."""

    flags: list[Flag]
    corrections: list[Correction]
    usd_total: Decimal | None


def validate_invoice(invoice: Invoice, deps: Deps) -> ValidationResult:
    """Check an invoice against the catalogue, the vendor master, and policy."""
    flags: list[Flag] = []
    corrections: list[Correction] = []

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

    flags.extend(_check_vendor(invoice, deps))
    flags.extend(_check_line_items(invoice, deps))
    flags.extend(_check_dates(invoice, deps))
    if invoice.purchase_order_reference is not None:
        flags.append(
            Flag(
                severity=FlagSeverity.INFO,
                code="po_reference_uncheckable",
                message=(
                    f"invoice cites purchase order {invoice.purchase_order_reference!r}; "
                    "no purchase-order records exist to match it against"
                ),
            )
        )

    usd_total, currency_flags, currency_corrections = _convert_to_usd(invoice)
    flags.extend(currency_flags)
    corrections.extend(currency_corrections)

    return ValidationResult(flags=flags, corrections=corrections, usd_total=usd_total)


def _check_vendor(invoice: Invoice, deps: Deps) -> list[Flag]:
    name = invoice.vendor.name.strip()
    if not name:
        return [
            Flag(
                severity=FlagSeverity.SOFT,
                code="empty_vendor",
                message="vendor name is missing or empty",
            )
        ]
    if not deps.catalogue.is_known_vendor(name):
        return [
            Flag(
                severity=FlagSeverity.SOFT,
                code="unknown_vendor",
                message=f"{name!r} is not in the vendor master",
            )
        ]
    return []


def _check_line_items(invoice: Invoice, deps: Deps) -> list[Flag]:
    flags: list[Flag] = []
    catalogue_items = {item.name: item for item in deps.catalogue.all_items()}

    quantities_by_normalized: dict[str, int] = {}
    display_name_by_normalized: dict[str, str] = {}
    catalogue_match_by_normalized: dict[str, CatalogueItem | None] = {}

    for line in invoice.line_items:
        if line.quantity < 0:
            flags.append(
                Flag(
                    severity=FlagSeverity.FATAL,
                    code="negative_quantity",
                    message=f"{line.item}: quantity {line.quantity} is negative",
                )
            )
            continue  # Excluded from aggregation: a corrupted line shouldn't mask or
            # reduce the aggregate quantity charged against the other, legitimate lines.

        normalized = _normalize_item_name(line.item)
        quantities_by_normalized[normalized] = (
            quantities_by_normalized.get(normalized, 0) + line.quantity
        )
        display_name_by_normalized[normalized] = line.item
        if normalized not in catalogue_match_by_normalized:
            catalogue_match_by_normalized[normalized] = _match_catalogue_item(
                line.item, catalogue_items
            )

        flags.extend(_check_price(line, catalogue_match_by_normalized[normalized]))

    for normalized, total_quantity in quantities_by_normalized.items():
        display_name = display_name_by_normalized[normalized]
        catalogue_item = catalogue_match_by_normalized[normalized]

        if catalogue_item is None:
            suggestion = _nearest_catalogue_name(display_name, catalogue_items)
            hint = f"; nearest catalogue entry {suggestion!r}" if suggestion else ""
            flags.append(
                Flag(
                    severity=FlagSeverity.SOFT,
                    code="unknown_item",
                    message=f"{display_name!r} is not in the catalogue{hint}",
                )
            )
            continue

        if catalogue_item.stock == 0:
            flags.append(
                Flag(
                    severity=FlagSeverity.FATAL,
                    code="zero_stock_item",
                    message=(
                        f"{display_name}: {total_quantity} requested, but Acme holds "
                        "zero stock of this item — likely fraudulent or discontinued"
                    ),
                )
            )
        elif total_quantity > catalogue_item.stock:
            flags.append(
                Flag(
                    severity=FlagSeverity.FATAL,
                    code="stock_exceeded",
                    message=(
                        f"{display_name}: invoice requests {total_quantity} across its "
                        f"line item(s), only {catalogue_item.stock} in stock"
                    ),
                )
            )

    return flags


def _check_price(line: LineItem, catalogue_item: CatalogueItem | None) -> list[Flag]:
    if catalogue_item is None or catalogue_item.expected_unit_price is None:
        return []
    if line.unit_price is None or line.unit_price <= catalogue_item.expected_unit_price:
        return []

    documented = bool(line.note)
    code = "price_above_expected_documented" if documented else "price_above_expected"
    qualifier = f" ({line.note})" if documented else ", with no explanation given"
    return [
        Flag(
            severity=FlagSeverity.SOFT,
            code=code,
            message=(
                f"{line.item}: billed at {line.unit_price}, catalogue expects "
                f"{catalogue_item.expected_unit_price}{qualifier}"
            ),
        )
    ]


def _check_dates(invoice: Invoice, deps: Deps) -> list[Flag]:
    flags: list[Flag] = []
    if invoice.due_date is not None and invoice.invoice_date is not None:
        if invoice.due_date < invoice.invoice_date:
            flags.append(
                Flag(
                    severity=FlagSeverity.SOFT,
                    code="due_date_before_invoice_date",
                    message=(
                        f"due date {invoice.due_date} is earlier than the invoice date "
                        f"{invoice.invoice_date}"
                    ),
                )
            )
    if invoice.due_date is not None and invoice.due_date < deps.clock.today():
        flags.append(
            Flag(
                severity=FlagSeverity.SOFT,
                code="due_date_in_the_past",
                message=f"due date {invoice.due_date} is already in the past",
            )
        )
    return flags


def _convert_to_usd(
    invoice: Invoice,
) -> tuple[Decimal | None, list[Flag], list[Correction]]:
    total = invoice.total if invoice.total is not None else invoice.line_items_total
    if total is None:
        return None, [], []

    if invoice.currency == "USD":
        return total, [], []

    rate = FX_RATES_TO_USD.get(invoice.currency)
    if rate is None:
        raise UnknownCurrency(
            f"no FX rate configured for {invoice.currency!r}; add one to FX_RATES_TO_USD"
        )

    converted = (total * rate).quantize(_ROUNDING_TOLERANCE)
    correction = Correction(
        field="usd_total",
        raw=f"{total} {invoice.currency}",
        value=f"{converted} USD",
        reason=f"converted at {rate} USD per {invoice.currency}",
        confidence=1.0,
    )
    flag = Flag(
        severity=FlagSeverity.SOFT,
        code="non_usd_currency",
        message=f"invoice is denominated in {invoice.currency}, not USD",
    )
    return converted, [flag], [correction]


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


def _normalize_item_name(name: str) -> str:
    return "".join(c for c in name.lower() if c.isalnum())


def _match_catalogue_item(
    item_name: str, catalogue_items: dict[str, CatalogueItem]
) -> CatalogueItem | None:
    normalized = _normalize_item_name(item_name)
    for catalogue_name, catalogue_item in catalogue_items.items():
        if _normalize_item_name(catalogue_name) == normalized:
            return catalogue_item
    return None


def _nearest_catalogue_name(
    item_name: str, catalogue_items: dict[str, CatalogueItem]
) -> str | None:
    """The closest catalogue entry name, for the flag text only — never used to match."""
    if not catalogue_items:
        return None
    normalized = _normalize_item_name(item_name)
    best_name, best_ratio = None, 0.0
    for catalogue_name in catalogue_items:
        ratio = SequenceMatcher(None, normalized, _normalize_item_name(catalogue_name)).ratio()
        if ratio > best_ratio:
            best_name, best_ratio = catalogue_name, ratio
    if best_ratio < _SUGGESTION_SIMILARITY_FLOOR:
        return None
    return best_name
