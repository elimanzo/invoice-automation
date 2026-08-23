# ADR-0010: Redundant independent critics for escalation (considered, deferred)

- **Status:** Considered, deferred
- **Date:** 2026-08-23

## Context

ADR-0004 makes approval a one-directional ratchet: deterministic rules decide, and a single
LLM critic may only move the outcome toward more caution, never release it. That closes the
prompt-injection risk, but it still depends on one model call correctly noticing a subtle
problem (as with INV-1008 — under threshold, no fatal flag, escalated only because the critic
read the vendor/items/tone as wrong). One critic missing that signal by chance means no
second chance to catch it.

The alternative on the table was a debate-style approach (two LLM roles arguing, seen in a
competing take-home submission on this same brief) where a challenger and a responder produce
the final call together. Rejected outright, not just deferred — see ADR-0004's rationale:
debate's defining feature is that argument can move the decision in *either* direction, which
reopens exactly the injection surface the ratchet exists to close. Debate is not on the table
here in any form.

## Decision

Considered instead: N (2-3) independent critic calls, each separately asked "does this invoice
look wrong," run in parallel over the same invoice and flags. If enough of them independently
flag it, that strengthens the case to escalate. None of them can ever downgrade or approve —
the ratchet's one-directional guarantee is unchanged; this only adds redundant scrutiny on the
"more caution" side.

**Deferred**, not built, for this round. It is additive to the existing ratchet (no redesign
of `approval.py`'s decision flow), but it is still new engineering — an aggregation rule across
N calls, a new schema, new tests — competing for time against higher-priority gaps (cassette
coverage, edge-case fixtures) with no rubric requirement it closes. The current single-critic
ratchet already satisfies the brief's "reflection or critique loop" language.

## Consequences if built later

**Good.** Harder to fool by a single critic call missing a signal or a document crafted to
target one specific prompt; a stronger fraud-resistance story for presentation.

**Bad.** N× the LLM calls (cost and latency) on every escalation path; another component to
test and keep in sync with the approval prompt; more failure modes to reason about (what
happens if the N critics disagree, or one errors out) with no guarantee it catches anything
the single critic doesn't already catch on this data set.

**Revisit when:** the single critic demonstrably misses a real escalation case in the eval
set, or presentation specifically calls for a stronger adversarial-fraud story.
