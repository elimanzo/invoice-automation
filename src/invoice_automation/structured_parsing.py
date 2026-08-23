"""Deterministic extraction for structured document formats.

Per ADR-0009: a JSON, CSV, or XML invoice is already extracted data. Reading it with code
is instant, free, and carries no hallucination risk that `json.loads`, `csv.reader`, or
`xml.etree` do not already have. The model is reserved for text, PDFs, email bodies, and
whatever structured input fails to parse here.

This module gets fields out of a document. It repairs nothing and judges nothing — a
negative quantity or an empty vendor name parses cleanly and reaches validation exactly as
stated, because deciding what a value means is validation's job, not parsing's.

Every parser here produces the same `Invoice` model as the LLM path
(`extraction.extract_invoice`), so nothing downstream can tell which one ran.
"""

from __future__ import annotations

import csv
import io
import json
import re
import xml.etree.ElementTree as ElementTree
from typing import Any

from pydantic import ValidationError

from .deps import Deps
from .documents import Document
from .models import DocumentFormat, Flag, FlagSeverity, Invoice

_MM_DD_YYYY = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


class StructuredParseFailed(Exception):
    """A structured document could not be parsed deterministically.

    Not a judgement about the invoice's content — only about whether this module could
    read it into the expected shape at all. The caller falls back to model extraction.
    """


def parse_structured(document: Document) -> Invoice:
    """Deterministically parse a JSON, CSV, or XML document into an invoice."""
    if document.format is DocumentFormat.JSON:
        return _parse_json(document.raw_text)
    if document.format is DocumentFormat.CSV:
        return _parse_csv(document.raw_text)
    if document.format is DocumentFormat.XML:
        return _parse_xml(document.raw_text)
    raise ValueError(f"{document.format} is not a structured format")


def _validate(payload: dict[str, Any], *, source: str) -> Invoice:
    try:
        return Invoice.model_validate(payload)
    except ValidationError as exc:
        raise StructuredParseFailed(
            f"{source} did not match the expected invoice shape: {exc}"
        ) from exc


def _normalize_date(value: Any) -> Any:
    """Convert the one non-ISO date syntax the sample data uses (MM/DD/YYYY) to ISO.

    Not a correction in the CONTEXT.md sense — no value is guessed or repaired, only
    reformatted. Anything that doesn't match either shape passes through unchanged and
    is left for Pydantic to accept or reject on its own.
    """
    if not isinstance(value, str):
        return value
    match = _MM_DD_YYYY.match(value.strip())
    if not match:
        return value
    month, day, year = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


def _parse_json(text: str) -> Invoice:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StructuredParseFailed(f"invalid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise StructuredParseFailed("top-level JSON value is not an object")

    line_items = [
        {
            "item": item.get("item"),
            "quantity": item.get("quantity"),
            "unit_price": item.get("unit_price"),
            "stated_amount": item.get("amount"),
            "note": item.get("note"),
        }
        for item in raw.get("line_items") or []
        if isinstance(item, dict)
    ]

    payload = {
        "invoice_number": raw.get("invoice_number"),
        "vendor": raw.get("vendor"),
        "invoice_date": _normalize_date(raw.get("date")),
        "due_date": _normalize_date(raw.get("due_date")),
        "line_items": line_items,
        "subtotal": raw.get("subtotal"),
        "tax_amount": raw.get("tax_amount"),
        "total": raw.get("total"),
        "currency": raw.get("currency") or "USD",
        "payment_terms": raw.get("payment_terms"),
        "notes": raw.get("notes"),
    }
    return _validate(payload, source="JSON")


# ---------------------------------------------------------------------------
# CSV — two shapes appear in the sample data: field,value and one-row-per-line-item.
# ---------------------------------------------------------------------------


def _parse_csv(text: str) -> Invoice:
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise StructuredParseFailed("empty CSV")

    header = [cell.strip().lower() for cell in rows[0]]
    if header == ["field", "value"]:
        return _parse_csv_field_value(rows[1:])
    return _parse_csv_tabular(text)


def _parse_csv_field_value(rows: list[list[str]]) -> Invoice:
    """`field,value` shape, e.g. invoice_1006.csv.

    'item', 'quantity', and 'unit_price' each repeat once per line item, in that order,
    immediately after each other — 'item' starts a new line item; the two rows after it
    belong to that item. Reading row by row rather than building a dict up front is what
    keeps every repetition instead of only the last (the trap ticket 04 flagged).
    """
    fields: dict[str, str] = {}
    line_items: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for row in rows:
        if len(row) < 2:
            continue
        key, value = row[0].strip(), row[1].strip()
        if key == "item":
            if current is not None:
                line_items.append(current)
            current = {"item": value}
        elif key in ("quantity", "unit_price") and current is not None:
            current[key] = value
        else:
            fields[key] = value
    if current is not None:
        line_items.append(current)

    payload = {
        "invoice_number": fields.get("invoice_number"),
        "vendor": {"name": fields.get("vendor")},
        "invoice_date": _normalize_date(fields.get("date")),
        "due_date": _normalize_date(fields.get("due_date")),
        "line_items": line_items,
        "subtotal": fields.get("subtotal"),
        "tax_amount": fields.get("tax"),
        "total": fields.get("total"),
        "payment_terms": fields.get("payment_terms"),
    }
    return _validate(payload, source="field,value CSV")


def _parse_csv_tabular(text: str) -> Invoice:
    """One row per line item, e.g. invoice_1007.csv, with trailing summary rows whose
    `Item` column is blank and whose label/value sit in `Unit Price`/`Line Total`."""
    reader = csv.DictReader(io.StringIO(text))
    line_items: list[dict[str, Any]] = []
    header_fields: dict[str, str | None] = {}
    subtotal = tax_amount = total = None

    for row in reader:
        item = (row.get("Item") or "").strip()
        if not item:
            label = (row.get("Unit Price") or "").strip().lower()
            value = (row.get("Line Total") or "").strip()
            if label.startswith("subtotal"):
                subtotal = value
            elif label.startswith("tax"):
                tax_amount = value
            elif label.startswith("total"):
                total = value
            continue

        if not header_fields:
            header_fields = {
                "invoice_number": row.get("Invoice Number"),
                "vendor": row.get("Vendor"),
                "date": row.get("Date"),
                "due_date": row.get("Due Date"),
            }
        line_items.append(
            {
                "item": item,
                "quantity": row.get("Qty"),
                "unit_price": row.get("Unit Price"),
                "stated_amount": row.get("Line Total"),
            }
        )

    if not header_fields:
        raise StructuredParseFailed("no line items found in tabular CSV")

    payload = {
        "invoice_number": header_fields.get("invoice_number"),
        "vendor": {"name": header_fields.get("vendor")},
        "invoice_date": _normalize_date(header_fields.get("date")),
        "due_date": _normalize_date(header_fields.get("due_date")),
        "line_items": line_items,
        "subtotal": subtotal,
        "tax_amount": tax_amount,
        "total": total,
    }
    return _validate(payload, source="tabular CSV")


# ---------------------------------------------------------------------------
# XML
# ---------------------------------------------------------------------------


def _text_of(parent: ElementTree.Element | None, tag: str) -> str | None:
    if parent is None:
        return None
    node = parent.find(tag)
    return node.text if node is not None else None


def _parse_xml(text: str) -> Invoice:
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise StructuredParseFailed(f"invalid XML: {exc}") from exc

    header = root.find("header")
    if header is None:
        raise StructuredParseFailed("missing <header> element")

    items_root = root.find("line_items")
    line_items = (
        [
            {
                "item": _text_of(item_el, "name"),
                "quantity": _text_of(item_el, "quantity"),
                "unit_price": _text_of(item_el, "unit_price"),
            }
            for item_el in items_root.findall("item")
        ]
        if items_root is not None
        else []
    )

    totals = root.find("totals")
    payload = {
        "invoice_number": _text_of(header, "invoice_number"),
        "vendor": {"name": _text_of(header, "vendor")},
        "invoice_date": _normalize_date(_text_of(header, "date")),
        "due_date": _normalize_date(_text_of(header, "due_date")),
        "currency": _text_of(header, "currency") or "USD",
        "line_items": line_items,
        "subtotal": _text_of(totals, "subtotal"),
        "tax_amount": _text_of(totals, "tax_amount"),
        "total": _text_of(totals, "total"),
        "payment_terms": _text_of(root, "payment_terms"),
    }
    return _validate(payload, source="XML")


# ---------------------------------------------------------------------------
# Cross-check: an explicit, opt-in mode. Never called from the main pipeline — running
# the model over every structured document would defeat the entire point of ADR-0009.
# ---------------------------------------------------------------------------

_CROSS_CHECK_FIELDS = ("invoice_number", "invoice_date", "due_date", "total", "currency")


def cross_check(document: Document, deps: Deps) -> list[Flag]:
    """Run both extraction paths over one structured document and flag disagreement.

    Because the deterministic parse is exact, it serves as ground truth here — this
    measures the model's accuracy against a known-correct answer, rather than against
    another opinion.
    """
    from .extraction import extract_invoice  # local import: avoids a cycle at module load

    deterministic = parse_structured(document)
    model_based = extract_invoice(document, deps)

    flags = []
    for field in _CROSS_CHECK_FIELDS:
        left, right = getattr(deterministic, field), getattr(model_based, field)
        if left != right:
            flags.append(
                Flag(
                    severity=FlagSeverity.INFO,
                    code="extraction_disagreement",
                    message=(
                        f"{field}: deterministic parse says {left!r}, "
                        f"model extraction says {right!r}"
                    ),
                )
            )

    if len(deterministic.line_items) != len(model_based.line_items):
        flags.append(
            Flag(
                severity=FlagSeverity.INFO,
                code="extraction_disagreement",
                message=(
                    f"line item count: deterministic parse found "
                    f"{len(deterministic.line_items)}, model extraction found "
                    f"{len(model_based.line_items)}"
                ),
            )
        )

    return flags
