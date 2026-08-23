# ADR-0005: Escalation interrupts the graph and resumes on human action

- **Status:** Accepted
- **Date:** 2026-08-22

## Context

Escalated invoices (ADR-0004) need a human decision, which may come hours later. The
question is what happens to the run in between. Options: suspend the graph and resume it;
terminate the run and start a fresh one that jumps to payment; or record decisions in a
display-only queue that resumes nothing.

## Decision

The graph interrupts at the approval node. LangGraph's checkpointer persists state; the CLI
reports "escalated, awaiting review" and **exits without blocking**. When a reviewer acts in
the dashboard, the run resumes from that exact node on that exact state.

## Consequences

**Good.** A genuine human-in-the-loop workflow rather than a simulation of one — the paused
run holds precisely the state the reviewer is shown, so there is nothing to re-derive and
nothing that can drift between what was approved and what gets paid. Nearly free, since the
checkpointer is already persisting state for run traces (ADR-0002).

**Good.** A non-blocking CLI means batch mode processes 20 invoices and reports escalations
rather than hanging on the first one.

**Bad.** Resumable state must stay compatible with the code that resumes it. A checkpoint
written before a state-schema change may not be resumable after it, which is fine for a
prototype and would need migration handling in production. Documented, not solved.
