"""Approval: the brief's threshold rule, plus the one rejection ticket 05 needs.

"Invoices over $10K require additional scrutiny." An invoice under it is approved, at or
over it is escalated.

A fatal flag now rejects outright — pulled forward from ticket 08 because ticket 05
needs to prove that a structured document can *parse* cleanly (no exception, no repair)
while still being *rejected* once validation looks at what it actually says. LLM
reasoning, the approval critic, and the caution ratchet (ADR-0004) are still ticket 08's;
this is only the deterministic floor that ratchet sits on top of.
"""

from __future__ import annotations

from decimal import Decimal

from .models import Decision, Flag, FlagSeverity, Invoice

SCRUTINY_THRESHOLD = Decimal("10000")


def decide(invoice: Invoice, flags: list[Flag] | None = None) -> Decision:
    """Apply the fatal-flag rule, then the brief's threshold rule."""
    flags = flags or []
    fatal = [flag for flag in flags if flag.severity is FlagSeverity.FATAL]
    if fatal:
        reasons = "; ".join(flag.message for flag in fatal)
        return Decision(outcome="rejected", reasoning=f"Fatal flag(s): {reasons}")

    total = invoice.total if invoice.total is not None else invoice.line_items_total
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

    return Decision(
        outcome="approved",
        reasoning=f"Total {total} is under the ${SCRUTINY_THRESHOLD} scrutiny threshold.",
    )
