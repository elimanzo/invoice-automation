"""Approval: the brief's threshold rule, a compounding risk score, and the one
rejection ticket 05 needed ahead of schedule.

"Invoices over $10K require additional scrutiny." An invoice under it is approved, at or
over it is escalated — and so is one whose soft flags compound past the risk threshold,
even while under the dollar line. That second path is what catches INV-1008: an unknown
vendor, two uncatalogued items, and a total of $9,900 sitting $100 under the scrutiny
line. No single rule stops it; the accumulated risk does.

A fatal flag rejects outright — pulled forward from ticket 05 because it needed to prove
that a structured document can *parse* cleanly while still being *rejected* once
validation looks at what it actually says. LLM reasoning, the approval critic, and the
caution ratchet (ADR-0004) are still ticket 08's; this is only the deterministic floor
that ratchet sits on top of.
"""

from __future__ import annotations

from decimal import Decimal

from .config import RISK_ESCALATION_THRESHOLD, RISK_WEIGHTS
from .models import Decision, Flag, FlagSeverity, Invoice

SCRUTINY_THRESHOLD = Decimal("10000")


def compute_risk_score(flags: list[Flag]) -> int:
    """The compounding weight of every soft flag. Fatal and info flags contribute
    nothing — fatal already rejects outright, and info is visibility only."""
    return sum(
        RISK_WEIGHTS.get(flag.code, 0) for flag in flags if flag.severity is FlagSeverity.SOFT
    )


def decide(
    invoice: Invoice,
    flags: list[Flag] | None = None,
    *,
    usd_total: Decimal | None = None,
) -> Decision:
    """Apply the fatal-flag rule, then the brief's threshold rule, then risk.

    `usd_total` is the amount already converted to USD (validation.py); when omitted,
    falls back to the invoice's own total, which is only correct for a USD invoice.
    """
    flags = flags or []
    fatal = [flag for flag in flags if flag.severity is FlagSeverity.FATAL]
    if fatal:
        reasons = "; ".join(flag.message for flag in fatal)
        return Decision(outcome="rejected", reasoning=f"Fatal flag(s): {reasons}")

    total = usd_total if usd_total is not None else invoice.total
    if total is None:
        total = invoice.line_items_total
    if total is None:
        return Decision(
            outcome="escalated",
            reasoning="No total could be determined; a human must confirm the amount.",
        )

    if total >= SCRUTINY_THRESHOLD:
        return Decision(
            outcome="escalated",
            reasoning=f"Total {total} meets or exceeds the ${SCRUTINY_THRESHOLD} scrutiny threshold.",
        )

    risk_score = compute_risk_score(flags)
    if risk_score >= RISK_ESCALATION_THRESHOLD:
        soft_codes = sorted({f.code for f in flags if f.severity is FlagSeverity.SOFT})
        return Decision(
            outcome="escalated",
            reasoning=(
                f"Risk score {risk_score} meets or exceeds the escalation threshold "
                f"{RISK_ESCALATION_THRESHOLD} ({', '.join(soft_codes)})."
            ),
        )

    return Decision(
        outcome="approved",
        reasoning=f"Total {total} is under the ${SCRUTINY_THRESHOLD} scrutiny threshold.",
    )
