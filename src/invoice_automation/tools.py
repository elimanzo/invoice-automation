"""Read-only tools the approval agent may call.

Per ADR-0004, reads are the model's job and writes are code's. Every function here does
exactly one SELECT-shaped thing against `Deps` and returns data — none can pay, record,
mutate the catalogue, or otherwise change anything. That is not a convention to remember;
it is checkable directly, since no function below holds a reference to anything but a
read method.

Each tool pairs a JSON schema (what the model sees) with a plain Python callable (what
actually runs). `TOOL_SPECS` is what gets offered to the provider; `dispatch` is what
executes whichever one the model asked for.
"""

from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
from typing import Any

from .catalogue import match_item
from .config import FX_RATES_TO_USD
from .deps import Deps

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "lookup_item",
            "description": "Look up a catalogue item's stock level and expected unit price.",
            "parameters": {
                "type": "object",
                "properties": {"item": {"type": "string"}},
                "required": ["item"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_stock",
            "description": "How many units of an item Acme currently holds.",
            "parameters": {
                "type": "object",
                "properties": {"item": {"type": "string"}},
                "required": ["item"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vendor_history",
            "description": (
                "Whether a vendor is in Acme's known-vendor master, and a summary of "
                "past payments made to them."
            ),
            "parameters": {
                "type": "object",
                "properties": {"vendor": {"type": "string"}},
                "required": ["vendor"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "prior_invoices",
            "description": "List of past invoices paid to a given vendor.",
            "parameters": {
                "type": "object",
                "properties": {"vendor": {"type": "string"}},
                "required": ["vendor"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fx_rate",
            "description": "The configured USD-per-unit exchange rate for a currency.",
            "parameters": {
                "type": "object",
                "properties": {"currency": {"type": "string"}},
                "required": ["currency"],
            },
        },
    },
]

TOOL_NAMES = frozenset(spec["function"]["name"] for spec in TOOL_SPECS)


def dispatch(name: str, arguments: dict[str, Any], deps: Deps) -> dict[str, Any]:
    """Run the named tool and return a JSON-shaped result.

    Raises `KeyError` for anything not in `TOOL_NAMES` — the caller's loop is the only
    thing that decides what happens next; this function never guesses at an unknown
    tool's intent.
    """
    if name == "lookup_item":
        return _lookup_item(arguments["item"], deps)
    if name == "check_stock":
        return _check_stock(arguments["item"], deps)
    if name == "vendor_history":
        return _vendor_history(arguments["vendor"], deps)
    if name == "prior_invoices":
        return _prior_invoices(arguments["vendor"], deps)
    if name == "fx_rate":
        return _fx_rate(arguments["currency"])
    raise KeyError(f"no such tool: {name!r}")


def _lookup_item(item: str, deps: Deps) -> dict[str, Any]:
    # match_item, not get_item: the same normalised matching validation.py uses
    # (ADR-0007). Using an exact match here once told the agent "Widget A" doesn't
    # exist when WidgetA plainly does, and it escalated an invoice on that false premise.
    entry = match_item(deps.catalogue, item)
    if entry is None:
        return {"found": False}
    return {
        "found": True,
        "stock": entry.stock,
        "expected_unit_price": (
            str(entry.expected_unit_price) if entry.expected_unit_price is not None else None
        ),
    }


def _check_stock(item: str, deps: Deps) -> dict[str, Any]:
    entry = match_item(deps.catalogue, item)
    return {"stock": entry.stock if entry is not None else None}


def _vendor_history(vendor: str, deps: Deps) -> dict[str, Any]:
    known = deps.catalogue.is_known_vendor(vendor)
    payments = [p for p in deps.registry.payments() if p.vendor == vendor]
    return {
        "known_vendor": known,
        "prior_payment_count": len(payments),
        "prior_payment_total": str(sum((p.amount for p in payments), Decimal(0))),
    }


def _prior_invoices(vendor: str, deps: Deps) -> dict[str, Any]:
    payments = [p for p in deps.registry.payments() if p.vendor == vendor]
    return {"invoices": [asdict(p) | {"amount": str(p.amount)} for p in payments]}


def _fx_rate(currency: str) -> dict[str, Any]:
    rate = FX_RATES_TO_USD.get(currency)
    return {"currency": currency, "usd_per_unit": str(rate) if rate is not None else None}
