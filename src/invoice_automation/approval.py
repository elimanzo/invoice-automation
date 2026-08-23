"""Approval: the one rule the brief states.

"Invoices over $10K require additional scrutiny." This ticket applies that threshold and
nothing else — an invoice under it is approved, an invoice at or over it is escalated.

Fatal-flag rejection, LLM reasoning, the approval critic, and the caution ratchet
(ADR-0004) all arrive with ticket 08. Until then, a fatal flag from validation is
recorded and visible in the result, but does not yet change the decision — the graph is
proving its shape before it grows judgement.
"""

from __future__ import annotations

from decimal import Decimal

from .models import Decision, Invoice

SCRUTINY_THRESHOLD = Decimal("10000")


def decide(invoice: Invoice) -> Decision:
    """Apply the brief's threshold rule."""
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
