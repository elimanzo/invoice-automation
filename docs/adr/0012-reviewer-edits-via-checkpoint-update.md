# ADR-0012: Reviewer edits reach the invoice via checkpoint update, not graph rewind

- **Status:** Accepted
- **Date:** 2026-08-23

## Context

Ticket 19 lets a VP fix a field on an escalated invoice from the review queue instead of
only approving or rejecting. Two things need deciding before that's buildable:

1. Editing a field invalidates the flags and risk score computed against the old value.
   Something has to recompute them before approve/reject makes sense. The graph-faithful
   option is a full re-entry from `validate` onward; the cheaper option is recomputing
   inline against checkpointed state.
2. Whichever option wins, the edited invoice has to be visible to `pay` when the VP finally
   approves — `resume_invoice` (ADR-0005) only continues forward from the `await_review`
   interrupt on whatever invoice is already in the checkpoint.

## Decision

Field edits are applied entirely in `api.py`, without touching `graph.py`:

- The review queue's submit action bundles any staged field edits together with the
  approve/reject outcome and reason into one request — never two round-trips for one
  review.
- If edits are present, the endpoint builds a new `Invoice` (models are frozen, so this is
  a replacement, not a mutation), writes a human-sourced `Correction` per edited field, and
  recomputes flags and risk score inline using the same `validation.py` functions the
  `validate` node already calls — no full graph re-entry.
- The edited invoice and new corrections are written into the run's checkpoint via
  LangGraph's `graph.update_state(config, {...}, as_node=None)`. `as_node` is deliberately
  `None`, not `"await_review"`: telling `update_state` the write *is* `await_review`'s
  completed output resolves the graph's routing past the interrupt immediately and the
  pending review vanishes without ever coming back. `as_node=None` just updates the
  channels and leaves `await_review` scheduled as the next pending task. The endpoint then
  calls `graph.invoke(None, config=config)`, which actually runs that pending task —
  `await_review` calls `interrupt()` again, this time against the edited state, producing a
  fresh pending interrupt with the same shape as the original escalation.
- `resume_invoice` is then called completely unchanged, against that fresh interrupt.
  `await_review` and `pay` never learn that an edit happened — they just see updated state
  and a routine second interrupt/resume cycle.
- A submission with no staged edits skips `update_state` entirely, so ticket 16's existing
  approve/reject flow is a strict subset of this one, not a variant of it.

`Correction` gains a `source: Literal["model", "human"]` field. A human edit's `confidence`
is `1.0` (it already means "how sure is the extraction of `value`," and a human directly
asserting a value is maximally sure — no separate sentinel needed). Its `raw` is the value
being overwritten — i.e. whatever was last stored for that field, not the original document
text several corrections back. `reason` is the VP's free text, falling back to `"reviewer
correction"` if left blank.

Editable fields are deliberately scoped: line-item fields (`quantity`, `unit_price`,
`stated_amount`, `note`, addressed by list index — `LineItem` gains no `id`) and header
fields that are typically typos (`due_date`, `invoice_date`, `payment_terms`,
`purchase_order_reference`, `notes`). Vendor identity, invoice number, and computed fields
(`amount`, `line_items_total`) stay read-only — a wrong value there usually means the wrong
document was matched, which is a reject-and-resubmit problem, not an edit.

An edit that itself introduces a new fatal flag does not block approval. Per ADR-0004's
caution ratchet, flags inform the human, they don't gate them — the VP sees the recomputed
flags in the same submission and decides anyway.

## Consequences

**Good.** `graph.py` and the `await_review`/`resume_invoice` contract are untouched. The
entire feature is additive in the API layer, which is a much smaller, more reviewable
change than teaching the graph a new re-entry point.

**Good.** Because edits and the outcome are one request, there's no window where the queue
shows a risk score computed against a value the VP already changed.

**Bad.** The `update_state` + `invoke(None, ...)` + `resume_invoice` sequence is a genuine
LangGraph subtlety — `as_node` must be `None`, not `"await_review"`, or the interrupt
resolves and vanishes instead of coming back. It works here because `await_review` is a
cheap, side-effect-free node up to its `interrupt()` call, so re-running it is harmless.
Fine for one editable interrupt; would need rethinking if a second edit-in-place node were
ever added, or if `await_review` grew a side effect before its `interrupt()` call.

**Bad.** `Correction.raw` no longer reliably means "the document's literal text" once a
human edit is involved — it means "whatever was stored a moment ago." This is a narrower
reading than CONTEXT.md's original definition and is called out there explicitly so it
doesn't get re-litigated per-callsite.
