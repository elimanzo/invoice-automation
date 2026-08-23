# ADR-0004: Approval is a caution ratchet

- **Status:** Accepted
- **Date:** 2026-08-22

## Context

The brief states one approval rule — "invoices over $10K require additional scrutiny" — and
asks the approval agent to "reason through approval/rejection with a reflection or critique
loop." One threshold cannot decide 16 invoices, and pure LLM judgement cannot be
regression-tested.

There is also a security dimension the brief does not raise. Invoice documents are untrusted
input, and some of them argue with the reader. INV-1003 reads "URGENT - Pay immediately to
avoid penalties" with a wire-transfer preference. That is prompt injection arriving through
the business document itself.

## Decision

Deterministic rules compute the outcome. The LLM then reasons about the invoice and the
flags, and may move the decision **only toward more caution**.

| Rules say | LLM may | LLM may not |
| --------- | --------------------- | ---------------------- |
| approve | escalate | — |
| escalate | confirm, or argue reject | approve |
| reject | confirm | approve, or downgrade |

The LLM's power is to stop things, never to release them.

## Consequences

**Good.** The floor is deterministic, so decisions are auditable and testable, and no
prompt-injected document can talk its way to payment. The ceiling is intelligent: INV-1008
passes every rule ($9,900 is under threshold, no fatal flag) but the LLM sees an unknown
vendor, two uncatalogued items, an emailed body rather than an invoice document, and a total
sitting $100 under the scrutiny line — and escalates. That invoice is caught by reasoning,
not by rules.

**Bad.** The ratchet can only ever add friction, so a legitimate invoice the rules mishandle
cannot be rescued by the model — it goes to a human. That is the correct direction for a
financial control, and it does mean the escalation queue carries some false positives.

**Consequence for testing.** Because rules decide and the model only tightens, decision
outcomes are assertable in tests; the reasoning text is scored by eval rather than asserted.
