"""Command-line interface.

`--invoice_path` is spelled exactly as the brief specifies, because that is the first
command anyone runs and it must work verbatim.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from .batch import BatchSummary, run_batch
from .catalogue import seed_catalogue
from .config import MissingApiKey, Settings
from .deps import build_deps
from .documents import (
    UndecodableDocument,
    UnreadableDocument,
    UnsupportedDocument,
    load_document,
)
from .extraction import ExtractionFailed
from .graph import RunResult, run_invoice
from .models import Decision, Flag, Invoice
from .payments import PaymentResult
from .providers import ProviderUnavailable

CHECKPOINT_FILENAME = "checkpoints.db"


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
        "--invoice_dir",
        type=Path,
        help=(
            "Path to a directory of invoice documents to process, one after another. "
            "One bad document does not stop the rest; a summary is reported at the end."
        ),
    )
    parser.add_argument(
        "--seed-catalogue",
        action="store_true",
        help="Reset the inventory catalogue to its seed contents and exit.",
    )
    parser.add_argument(
        "--provider",
        choices=["grok", "fake"],
        default=None,
        help=(
            "Force a reasoning provider. Default: grok if XAI_API_KEY is set, "
            "fake otherwise."
        ),
    )
    args = parser.parse_args(argv)

    settings = Settings.from_env()

    if args.invoice_path is not None and args.invoice_dir is not None:
        print(
            "error: --invoice_path and --invoice_dir are mutually exclusive. "
            "Pass one or the other.",
            file=sys.stderr,
        )
        return 2

    if args.seed_catalogue and (args.invoice_path is not None or args.invoice_dir is not None):
        print(
            "error: --seed-catalogue resets the catalogue and processes nothing. "
            "Run it on its own, then process invoices.",
            file=sys.stderr,
        )
        return 2

    if args.seed_catalogue:
        path = Path(settings.data_dir) / settings.catalogue_filename
        seed_catalogue(path, reset=True)
        print(f"Catalogue reset to seed contents: {path}")
        return 0

    if args.invoice_path is None and args.invoice_dir is None:
        parser.print_help()
        return 2

    if args.invoice_dir is not None:
        if not args.invoice_dir.is_dir():
            print(f"error: no such directory: {args.invoice_dir}", file=sys.stderr)
            return 1

        try:
            deps = build_deps(settings, provider=args.provider)
        except MissingApiKey as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

        checkpoint_path = Path(settings.data_dir) / CHECKPOINT_FILENAME
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(checkpoint_path, check_same_thread=False)) as conn:
            summary = run_batch(args.invoice_dir, deps, checkpointer=SqliteSaver(conn))

        print(render_batch(summary))
        return 0

    assert args.invoice_path is not None  # the two branches above are exhaustive

    try:
        document = load_document(args.invoice_path)
    except (
        FileNotFoundError,
        UnsupportedDocument,
        UndecodableDocument,
        UnreadableDocument,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        deps = build_deps(settings, provider=args.provider)
    except MissingApiKey as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    checkpoint_path = Path(settings.data_dir) / CHECKPOINT_FILENAME
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(checkpoint_path, check_same_thread=False)) as conn:
        checkpointer = SqliteSaver(conn)
        try:
            result = run_invoice(document, deps, checkpointer=checkpointer)
        except (ExtractionFailed, LookupError, ProviderUnavailable) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    print(render(result, document_name=document.name))
    return 0


def render_batch(summary: BatchSummary) -> str:
    """One line per document, then the counts a clerk actually wants to see."""
    lines = [f"{item.document_name:<30} {item.outcome}" for item in summary.items]
    lines.append("")
    lines.append(f"Processed: {len(summary.items)}")
    for outcome, count in sorted(summary.counts.items()):
        lines.append(f"  {outcome}: {count}")
    return "\n".join(lines)


def render(result: RunResult, *, document_name: str) -> str:
    """Human-readable rendering of a run's outcome."""
    assert result.invoice is not None  # run_invoice raises before this point otherwise
    lines = [f"Document:  {document_name}", *_invoice_lines(result.invoice), ""]

    lines.append("Flags:")
    lines += [f"  [{flag.severity.value:>5}] {flag.message}" for flag in result.flags] or [
        "  (none)"
    ]
    lines.append("")

    lines.append("Corrections:")
    lines += [
        f"  {c.field}: {c.raw!r} -> {c.value!r}  ({c.reason}, confidence {c.confidence:.2f})"
        for c in result.corrections
    ] or ["  (none)"]
    lines.append("")

    lines.append("Approval agent investigation:")
    lines += [
        f"  {tc.name}({', '.join(f'{k}={v!r}' for k, v in tc.arguments.items())}) -> {tc.result}"
        for tc in result.tool_calls
    ] or ["  (none)"]
    lines.append("")

    lines += _decision_lines(result.decision)
    lines += _payment_lines(result.payment)
    return "\n".join(lines)


def _invoice_lines(invoice: Invoice) -> list[str]:
    lines = [
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
    return lines


def _decision_lines(decision: Decision | None) -> list[str]:
    if decision is None:
        return ["Decision:  (none)"]
    return [
        f"Decision:  {decision.outcome.upper()}",
        f"Reasoning: {decision.reasoning}",
        "",
    ]


def _payment_lines(payment: PaymentResult | None) -> list[str]:
    if payment is None:
        return ["Payment:   not made"]
    return [f"Payment:   {payment.status} ({payment.reference})"]


def _money(value: object) -> str:
    """Right-aligned to a fixed width, so a missing value keeps the column."""
    return f"{'(none)' if value is None else value:>10}"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
