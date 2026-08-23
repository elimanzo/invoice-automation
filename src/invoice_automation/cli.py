"""Command-line interface.

`--invoice_path` is spelled exactly as the brief specifies, because that is the first
command anyone runs and it must work verbatim.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .catalogue import seed_catalogue
from .config import Settings
from .deps import build_deps
from .documents import UnsupportedDocument, load_document
from .extraction import ExtractionFailed, extract_invoice
from .models import Invoice


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="invoice-automation",
        description="Process a supplier invoice: extract, validate, approve, pay.",
    )
    parser.add_argument(
        "--invoice_path",
        type=Path,
        help="Path to a single invoice document to process.",
    )
    parser.add_argument(
        "--seed-catalogue",
        action="store_true",
        help="Reset the inventory catalogue to its seed contents and exit.",
    )
    args = parser.parse_args(argv)

    settings = Settings.from_env()

    if args.seed_catalogue:
        path = Path(settings.data_dir) / settings.catalogue_filename
        seed_catalogue(path, reset=True)
        print(f"Catalogue reset to seed contents: {path}")
        return 0

    if args.invoice_path is None:
        parser.print_help()
        return 2

    try:
        document = load_document(args.invoice_path)
    except (FileNotFoundError, UnsupportedDocument) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    deps = build_deps(settings)

    try:
        invoice = extract_invoice(document, deps)
    except (ExtractionFailed, LookupError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(render(invoice, document_name=document.name))
    return 0


def render(invoice: Invoice, *, document_name: str) -> str:
    """Human-readable rendering of an extracted invoice."""
    lines = [
        f"Document:  {document_name}",
        f"Invoice:   {invoice.invoice_number or '(none stated)'}",
        f"Vendor:    {invoice.vendor.name or '(none stated)'}",
        f"Dated:     {invoice.invoice_date or '(none stated)'}",
        f"Due:       {invoice.due_date or '(none stated)'}",
        f"Terms:     {invoice.payment_terms or '(none stated)'}",
        "",
        "Line items:",
    ]
    for item in invoice.line_items:
        note = f"   [{item.note}]" if item.note else ""
        lines.append(
            f"  {item.item:<14} qty {item.quantity:>4}"
            f"  @ {_money(item.unit_price)}"
            f"  = {_money(item.amount)}{note}"
        )
    lines += [
        "",
        f"Subtotal:  {_money(invoice.subtotal)} {invoice.currency}",
        f"Tax:       {_money(invoice.tax_amount)} {invoice.currency}",
        f"Total:     {_money(invoice.total)} {invoice.currency}",
        f"Line sum:  {_money(invoice.line_items_total)} {invoice.currency}",
    ]
    if invoice.purchase_order_reference:
        lines.append(f"PO ref:    {invoice.purchase_order_reference}")
    return "\n".join(lines)


def _money(value: object) -> str:
    return "(none)" if value is None else f"{value:>10}"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
