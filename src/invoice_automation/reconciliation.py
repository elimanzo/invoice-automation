"""Reconciliation: what happens when a second document arrives for an identity
already seen.

Nothing in the brief asks for this, but the sample data cannot be handled correctly
without it: INV-1011 and INV-1012 each arrive as two documents (a PDF and a text twin)
that don't always agree, and INV-1004 has a revision that raises its total by $4,050.
Processing each document as if it were the first time overpays.

Four outcomes, in the order they're checked:

1. **Revision** — the document declares itself a replacement (`Invoice.revision` is
   set). It supersedes what came before. If the original was already paid, that's an
   exception, not a flag — code cannot decide on its own whether to claw back a payment
   or issue a supplement; a human must.
2. **Identical** — every field the two documents both state agrees, once whitespace is
   normalised. The second document is redundant; an info flag notes it, nothing merges.
3. **Contradiction** — the two documents disagree on a field they both state. Never
   resolved automatically — flagged heavily enough to force escalation on its own
   (RISK_WEIGHTS["duplicate_contradiction"] equals the escalation threshold), showing
   both values, so a human decides which is right.
4. **Enrichment** — one document states a field the other doesn't, with no
   disagreement. Merged, preferring whichever document actually stated each field, with
   a soft flag naming what the thinner one was missing.

Applies uniformly regardless of how a document was extracted — deterministic parsing and
model extraction both produce an `Invoice`, and reconciliation only ever looks at that.
"""

from __future__ import annotations

from dataclasses import dataclass

from .deps import Deps
from .models import Correction, Flag, FlagSeverity, Invoice
from .registry import normalize_invoice_identity

# The header-level fields reconciliation compares. Two things deliberately excluded:
#
# Line items — a meaningful disagreement there already surfaces as a total/subtotal
# mismatch (validation.py) or a stock-aggregation finding, and diffing items
# field-by-field is a different, harder problem this ticket's real cases don't need —
# every genuine duplicate pair in the sample data (INV-1011, INV-1012) agrees on items
# and differs only in header fields a PDF's layout happened to drop.
#
# Vendor — unlike every field below, `Invoice.vendor` is required, never None, so it
# can never register as "one side is missing this." Comparing it here would mean any
# two documents with even a trivially different vendor object (a stray space, an
# address one has and the other doesn't) hard-contradict, which is a much more
# aggressive trigger than this ticket's real cases call for.
_COMPARABLE_FIELDS = (
    "invoice_date",
    "due_date",
    "subtotal",
    "tax_amount",
    "total",
    "currency",
    "payment_terms",
    "purchase_order_reference",
)


class RevisionAfterPayment(Exception):
    """A revision arrived for an invoice that has already been paid."""


@dataclass(frozen=True)
class ReconciliationResult:
    invoice: Invoice
    flags: list[Flag]
    corrections: list[Correction]


def reconcile(invoice: Invoice, document_name: str, deps: Deps) -> ReconciliationResult:
    """Reconcile `invoice` against whatever was previously seen for its identity."""
    identity = normalize_invoice_identity(invoice.invoice_number)
    if identity is None:
        return ReconciliationResult(invoice=invoice, flags=[], corrections=[])

    seen = deps.registry.get_seen_invoice(identity)
    if seen is None:
        deps.registry.record_seen_invoice(identity, document_name, invoice.model_dump(mode="json"))
        return ReconciliationResult(invoice=invoice, flags=[], corrections=[])

    previous = Invoice.model_validate(seen.invoice)

    if invoice.revision is not None:
        return _reconcile_revision(invoice, previous, identity, document_name, deps)

    conflicts = _conflicting_fields(invoice, previous)
    if conflicts:
        return _reconcile_contradiction(invoice, previous, conflicts)

    missing_in_current, missing_in_previous = _asymmetric_fields(invoice, previous)
    if not missing_in_current and not missing_in_previous:
        return _reconcile_identical(invoice, identity, seen.document_name)

    return _reconcile_enrichment(
        invoice, previous, identity, document_name, missing_in_current, deps
    )


def _reconcile_revision(
    invoice: Invoice, previous: Invoice, identity: str, document_name: str, deps: Deps
) -> ReconciliationResult:
    if deps.registry.payment_recorded(identity):
        delta = (invoice.total or 0) - (previous.total or 0)
        raise RevisionAfterPayment(
            f"invoice {identity} was revised (revision {invoice.revision!r}) after "
            f"payment; the revision changes the total by {delta}. A human must decide "
            "whether to claw back or supplement the payment already made."
        )
    deps.registry.record_seen_invoice(identity, document_name, invoice.model_dump(mode="json"))
    flag = Flag(
        severity=FlagSeverity.INFO,
        code="revision_supersedes",
        message=(
            f"revision {invoice.revision!r} supersedes the prior version of invoice "
            f"{identity} ({previous.total} -> {invoice.total})"
        ),
    )
    return ReconciliationResult(invoice=invoice, flags=[flag], corrections=[])


def _reconcile_identical(invoice: Invoice, identity: str, previous_document: str) -> ReconciliationResult:
    flag = Flag(
        severity=FlagSeverity.INFO,
        code="duplicate_document",
        message=(
            f"identical to {previous_document!r}, already seen for invoice {identity}; "
            "this document adds nothing new"
        ),
    )
    return ReconciliationResult(invoice=invoice, flags=[flag], corrections=[])


def _reconcile_contradiction(
    invoice: Invoice, previous: Invoice, conflicts: list[str]
) -> ReconciliationResult:
    flags = [
        Flag(
            severity=FlagSeverity.SOFT,
            code="duplicate_contradiction",
            message=(
                f"{field}: this document says {_field_value(invoice, field)!r}, a prior "
                f"document says {_field_value(previous, field)!r} — not resolved "
                "automatically"
            ),
        )
        for field in conflicts
    ]
    # Never pick a winner: the current document proceeds unmerged. The flag's weight
    # (config.RISK_WEIGHTS) guarantees escalation on its own, regardless of anything
    # else about the invoice.
    return ReconciliationResult(invoice=invoice, flags=flags, corrections=[])


def _reconcile_enrichment(
    invoice: Invoice,
    previous: Invoice,
    identity: str,
    document_name: str,
    missing_in_current: list[str],
    deps: Deps,
) -> ReconciliationResult:
    updates = {
        field: _field_value(previous, field)
        for field in missing_in_current
        if _field_value(previous, field) is not None
    }
    merged = invoice.model_copy(update=updates) if updates else invoice

    corrections = [
        Correction(
            field=field,
            raw="(missing)",
            value=str(_field_value(previous, field)),
            reason=f"filled in from a prior document seen for invoice {identity}",
            confidence=1.0,
        )
        for field in updates
    ]
    flags = []
    if missing_in_current:
        flags.append(
            Flag(
                severity=FlagSeverity.SOFT,
                code="duplicate_enriched",
                message=(
                    f"this document omitted {', '.join(missing_in_current)}; filled in "
                    "from a prior document for the same invoice"
                ),
            )
        )

    deps.registry.record_seen_invoice(identity, document_name, merged.model_dump(mode="json"))
    return ReconciliationResult(invoice=merged, flags=flags, corrections=corrections)


def _field_value(invoice: Invoice, field: str) -> object:
    return getattr(invoice, field)


def _normalize_str(value: object) -> object:
    """Collapse whitespace before comparing strings — a PDF's line wrapping shouldn't
    register as a contradiction against a text file's single-line version."""
    if isinstance(value, str):
        return " ".join(value.split())
    return value


def _conflicting_fields(invoice: Invoice, previous: Invoice) -> list[str]:
    conflicts = []
    for field in _COMPARABLE_FIELDS:
        current_value = _normalize_str(_field_value(invoice, field))
        previous_value = _normalize_str(_field_value(previous, field))
        if current_value is not None and previous_value is not None and current_value != previous_value:
            conflicts.append(field)
    return conflicts


def _asymmetric_fields(invoice: Invoice, previous: Invoice) -> tuple[list[str], list[str]]:
    """Fields the current document lacks that the previous one stated, and vice versa.
    Assumes no conflicts — call after `_conflicting_fields` returns empty."""
    missing_in_current = [
        f
        for f in _COMPARABLE_FIELDS
        if _field_value(invoice, f) is None and _field_value(previous, f) is not None
    ]
    missing_in_previous = [
        f
        for f in _COMPARABLE_FIELDS
        if _field_value(previous, f) is None and _field_value(invoice, f) is not None
    ]
    return missing_in_current, missing_in_previous
