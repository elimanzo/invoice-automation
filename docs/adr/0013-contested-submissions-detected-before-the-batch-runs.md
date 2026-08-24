# ADR-0013: Contested submissions are detected before the batch runs

- **Status:** Accepted
- **Date:** 2026-08-23

## Context

`run_batch` processes a directory in sorted filename order. `data/invoices/` contains both
`invoice_1004.json` (total $1,890) and `invoice_1004_revised.json` (revision `R1`, total
$5,940) — the same invoice, INV-1004, twice.

Sorted order reaches the original first. It is clean, from a known vendor, well under the
$10K scrutiny threshold, and nothing about it looks wrong, because nothing about it *is*
wrong in isolation — so it auto-approved and paid $1,890. The revision was read next, and
reconciliation ([ADR-0010's sibling logic](../../src/invoice_automation/reconciliation.py))
correctly refused to resolve it: the obligation it supersedes had already been settled, so
it raised `RevisionAfterPayment` rather than paying the difference or ignoring it.

That exception is the right response to the situation, and it arrives too late to matter.
$1,890 has left the building against an invoice that was never for $1,890, and the batch
summary reports `failed: 1` — a document that was, in fact, read and understood perfectly.

Reversing the sort order does not fix it; it relocates it. The revision would pay $5,940
first, and the original would then be rejected as a stale duplicate. Two different answers
for the same two documents, chosen by nothing but the filenames. `CONTEXT.md` states that a
revision supersedes the original; under batch ordering that guarantee held only when the
filesystem happened to agree.

The single-document CLI path (`--invoice_path`) has the same exposure across separate
invocations, and there it is genuinely unavoidable: when the original is processed, the
revision does not yet exist as far as this system is concerned. A batch is different. Every
document is present and readable before the first one runs.

## Decision

Before any document in a batch is processed, scan the whole directory for **contested
submissions**: two or more documents claiming the same invoice identity that *disagree
about which of them is current* — a revision alongside a non-revision, or two different
revision labels. Every document in such a group is held — a soft flag whose weight equals
`RISK_ESCALATION_THRESHOLD`, so it forces escalation on its own — and a reviewer says which
version is current.

Three boundaries:

1. **Only disagreement contests.** Documents making the same claim are an ordinary
   duplicate or enrichment, and reconciliation already resolves those correctly whichever
   arrives first (INV-1011, INV-1012). Holding them would convert a solved case into manual
   work — the opposite of this system's purpose. The first version of this decision said
   "at least one declares a revision", which caught two *copies of the same revision* and
   blocked the unambiguously current invoice from ever paying — strictly worse than the bug
   being fixed. Found in review; the rule is disagreement, pinned by
   `test_two_copies_of_one_revision_are_not_contested`.
2. **Escalated, not rejected.** One of the two totals is almost certainly owed. The system's
   failure here was guessing which, not paying at all.
3. **Identity is read without the model.** Structured documents (JSON/CSV/XML) parse
   deterministically ([ADR-0009](0009-deterministic-parsing-for-structured-formats.md)), so
   their invoice number and `revision` field are free to inspect. A `.txt` or `.pdf`
   document's identity is only known after extraction. PDFs are skipped by suffix before
   loading, not by format after: text-extracting one is the most expensive load in a batch,
   and a PDF can never qualify anyway.

Identity here is `normalize_invoice_identity` — the digit run, vendor not considered, the
same notion `registry` keys payments on. Two vendors independently numbering an invoice
`INV-1004` therefore collide, and the hold is the conservative end of that pre-existing
ambiguity: payment idempotency already treats them as one obligation and would pay only
whichever arrived first. A vendor tiebreak belongs in `normalize_invoice_identity`, for the
whole system, not in this module alone.

The flag is raised in the `reconcile` node rather than seeded into the graph's initial
`flags`, because `_ingest`'s model path *replaces* that state key and would silently drop a
pre-seeded flag. Reconciliation is also where it belongs: a contested submission is a
reconciliation finding that happens to be knowable earlier than reconciliation runs.

## Consequences

**Good.** The revision-supersedes guarantee no longer depends on filename order — proved by
a test that reverses it and asserts the outcome does not move. The batch's one `failed`
document becomes two explained escalations. `RevisionAfterPayment` remains as the backstop
for the cross-invocation case it was written for, still covered by its own test.

**Bad.** Two documents that a human would have waved through now cost a human's attention.
Accepted: minutes of review against the difference between two totals, and the reviewer sees
both numbers rather than discovering the gap in a bank statement.

**Bad.** A revision arriving as `.txt` or `.pdf` is invisible to the pre-scan, so that pair
still resolves in arrival order. Closing the gap means extracting every document before
processing any of them — paying the model twice per document across the whole batch — to fix
a case the sample data does not contain and real AP mail would deliver as structured EDI or
a PDF pair reconciliation already handles one document later. Revisited if a text-format
revision ever shows up; the batch's own summary is where it would surface.

**Bad.** A repeated revision label no longer takes reconciliation's revision path
(`reconciliation.reconcile`), which is what makes two copies of one revision resolve as the
duplicate they are instead of raising `RevisionAfterPayment` on the second. That is a
correction to reconciliation's outcome ordering, not to this pre-scan — the pre-scan only
made it visible.

**Bad.** Two golden-set expectations changed (`evals.py`), one from "errors" to "escalated".
The eval harness measures agreement against recorded ground truth, so ground truth that
encoded a defect had to be rewritten rather than the score being left to drift. Both cases
carry a note saying what they used to expect and why.
