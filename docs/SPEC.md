# Spec: Multi-agent invoice processing pipeline

Vocabulary: [CONTEXT.md](../CONTEXT.md). Decisions: [adr/](adr/).
Original brief: [REQUIREMENTS.md](../REQUIREMENTS.md).

## Problem Statement

Acme Corp's accounts-payable staff process supplier invoices by hand. A clerk opens whatever
arrived by email — a PDF, a spreadsheet export, sometimes just an email body — reads the
vendor, total, line items, and due date off it, checks each item against a legacy inventory
database, emails a VP for approval, waits, and finally triggers payment.

The documents fight them. Field names are misspelled (`Vndr`, `Itms`, `Amt`). Dates are
corrupted by OCR (`26-Jan-2O26`). Amounts contain letter O for zero (`$3,500.O0`). Items are
spelled inconsistently (`Widget A` for `WidgetA`) or don't exist at all (`SuperGizmo`).
Quantities go negative. Vendors apply pressure ("URGENT - Pay immediately to avoid
penalties"). The same invoice arrives twice in different formats, and sometimes the two
copies disagree.

The result: **$2M/year in processing cost, a 30% error rate, and 5-day turnaround**. Nobody
can answer "why was this invoice paid?" after the fact, because the reasoning lived in a
clerk's head and an email thread.

## Solution

An automated pipeline that takes a document and produces a **decision** — paid, rejected, or
escalated to a human — with a complete, inspectable record of how it got there.

From the clerk's side: point it at a file, or at a directory of files, and watch the results.
From the VP's side: only the invoices that genuinely need judgement arrive, each with the
system's recommendation and reasoning attached. From the controller's side: every
**correction** the system made to a document, every **flag** it raised, and every decision it
took, queryable after the fact.

Four stages run as a graph: **ingestion** (document to structured invoice, with corrections),
**validation** (invoice against the catalogue, producing flags), **approval** (deterministic
rules examined by an LLM critic under the caution ratchet), and **payment** (mocked,
idempotent). Critique loops in ingestion and approval let the system catch its own mistakes
before a human sees them.

Two interfaces: a CLI that matches the brief exactly, and a local web dashboard with three
views plus a drill-down.

## User Stories

### Ingestion

1. As an AP clerk, I want to process a single invoice by passing its path on the command
   line, so that I can handle a one-off without opening a UI.
2. As an AP clerk, I want to process a whole directory of invoices in one command, so that I
   can clear an inbox in one pass.
3. As an AP clerk, I want plain-text, JSON, CSV, XML, and PDF documents all accepted, so that
   I never have to convert a file by hand first.
4. As an AP clerk, I want the system to read an emailed message body as an invoice, so that
   INV-1008-style forwarded mail is not a dead end.
5. As an AP clerk, I want misspelled field labels understood (`Vndr` as vendor, `Amt` as
   total), so that INV-1002 does not need manual retyping.
6. As an AP clerk, I want OCR corruption repaired (`2O26` to `2026`, `$3,500.O0` to
   `3500.00`), so that INV-1012 processes without me correcting characters.
7. As an AP clerk, I want dates in any format normalised (`Jan 30 2026`, `01/28/2026`,
   `26-Jan-2026`, `January 27, 2026`), so that format variation is not my problem.
8. As an AP clerk, I want a PDF with no extractable text to fail with a clear message naming
   the file and the reason, so that I know to handle it manually rather than assume it
   worked.
9. As a controller, I want every difference between what a document said and what the system
   stored recorded as a **correction** with field, raw value, stored value, reason, and
   confidence, so that I can audit any figure back to its source.
10. As a controller, I want a correction the system is not confident about to escalate rather
    than be applied silently, so that guesses never reach payment.
11. As a controller, I want INV-1003's `Due Date: yesterday` treated as unresolvable and
    flagged, so that the system never invents a date it cannot derive.
12. As an engineer, I want an extraction **critic** to check the extracted invoice against
    the raw document text and trigger a bounded re-extraction when it finds problems, so that
    obvious extraction errors self-correct before validation.
13. As an engineer, I want the critique loop capped at a fixed number of retries, so that a
    pathological document cannot spin forever or burn unbounded tokens.
14. As an engineer, I want the LLM constrained to a schema via tool/function definitions and
    the result validated, so that malformed structured output is caught rather than
    propagated.
15. As an engineer, I want a schema validation failure fed back to the model as feedback for
    one bounded retry, so that a `"quantity": "five"` response self-corrects to `5`.
16. As an AP clerk, I want multiple line items for the same item preserved separately
    (INV-1013 bills WidgetA three times at three prices), so that discounts and premiums are
    not silently merged away.
17. As an AP clerk, I want an invoice whose stated total disagrees with the sum of its line
    items flagged, so that arithmetic errors surface.
18. As an AP clerk, I want non-item charges like INV-1010's `Shipping: $150.00` recognised as
    charges rather than treated as unknown items, so that legitimate freight is not flagged as
    fraud.

### Validation

19. As an AP clerk, I want each line item checked against the inventory **catalogue**, so that
    I never pay for something Acme does not stock.
20. As an AP clerk, I want a quantity exceeding stock on hand flagged (INV-1002 requests 20
    GadgetX against 5 in stock), so that impossible orders are caught.
21. As an AP clerk, I want quantities aggregated across line items before the stock check
    (INV-1013's WidgetA totals 22 across four lines against 15 in stock), so that splitting an
    order across lines cannot evade the check.
22. As an AP clerk, I want an item absent from the catalogue flagged as unknown (INV-1016's
    WidgetC, INV-1008's SuperGizmo and MegaSprocket), so that invented products are caught.
23. As an AP clerk, I want item names matched after normalisation so `Widget A` and `Gadget X`
    resolve to real catalogue entries, so that spacing and casing are not false alarms.
24. As a controller, I want an unmatched item to stay unknown even when it closely resembles a
    catalogue entry, with the near-match named in the flag text only, so that no invented
    match ever reaches a decision (ADR-0007).
25. As an AP clerk, I want a zero-stock item treated as a serious finding (INV-1003's
    FakeItem), so that fraudulent line items stop the invoice.
26. As an AP clerk, I want a negative quantity treated as a fatal data-integrity failure
    (INV-1009), so that a negative invoice can never be paid.
27. As an AP clerk, I want a missing or empty vendor name flagged (INV-1009), so that
    unattributable invoices do not proceed.
28. As an AP clerk, I want a vendor absent from the known-vendors table flagged (Fraudster
    LLC, NoProd Industries), so that unfamiliar payees get scrutiny.
29. As an AP clerk, I want a unit price above the catalogue's expected price flagged softly,
    so that padded pricing is visible without blocking legitimate premiums.
30. As an AP clerk, I want a documented premium or discount (INV-1010's `rush order`,
    INV-1013's `Volume discount`) to carry lower weight than an unexplained one, so that
    normal commercial variation does not create noise.
31. As an AP clerk, I want a due date earlier than the invoice date flagged (INV-1002 is due
    the same day it is dated), so that impossible payment terms surface.
32. As an AP clerk, I want a due date already in the past flagged, so that urgency pressure is
    visible as a signal rather than acted on.
33. As a controller, I want every flag to carry a severity of fatal, soft, or info, so that
    the pipeline's response to a finding is predictable.
34. As a controller, I want soft flags accumulated into a **risk score**, so that several
    individually tolerable findings can together force review.
35. As a controller, I want INV-1008 escalated on its risk score despite passing every
    individual rule, so that a total placed just under the scrutiny threshold does not evade
    review.

### Currency

36. As a controller, I want each invoice's currency captured explicitly, so that a figure is
    never ambiguous about its unit.
37. As a controller, I want non-USD amounts converted to USD before any threshold comparison
    (INV-1014 is in EUR), so that the $10K scrutiny rule means the same thing for every
    invoice.
38. As a controller, I want the conversion recorded as an auditable correction naming the rate
    used, so that a converted figure can be traced.
39. As a controller, I want a non-USD invoice to carry a soft flag, so that currency risk is
    visible even when conversion succeeds.

### Duplicates, revisions, reconciliation

40. As a controller, I want an invoice paid at most once regardless of how many documents
    carry it, so that duplicate submissions cannot cause duplicate payment.
41. As a controller, I want payment keyed on invoice number in a **registry**, so that
    re-running a batch is safe.
42. As an AP clerk, I want a second document for an invoice already processed to be
    reconciled rather than processed afresh, so that INV-1011 arriving as both PDF and TXT
    does not pay twice.
43. As an AP clerk, I want two identical copies to be recognised and the duplicate dropped
    quietly (INV-1012's PDF and TXT match once whitespace is normalised), so that harmless
    duplication creates no noise.
44. As an AP clerk, I want a richer copy to supplement a thinner one, with the gap soft-flagged
    (INV-1011's PDF omits subtotal, tax, and terms that its TXT twin carries), so that no
    field is lost to whichever file was read first.
45. As a controller, I want two copies that contradict each other on the same field to be
    hard-flagged and escalated with both values shown, so that the system never picks a
    winner on its own.
46. As a controller, I want a document declaring itself a revision to supersede the original
    (`invoice_1004_revised.json` raises INV-1004 from $1,890 to $5,940), so that the current
    obligation is what gets paid.
47. As a controller, I want a revision arriving after its original was already paid to raise
    an exception naming the delta, so that an underpayment or overpayment is caught rather
    than buried.
48. As a controller, I want an invoice number missing its usual prefix (INV-1002 is bare
    `1002`) still matched to the right identity, so that formatting does not defeat duplicate
    detection.

### Approval

49. As a VP, I want invoices decided by explicit rules first, so that outcomes are consistent
    and auditable rather than a matter of model mood.
50. As a VP, I want an invoice over $10K to require additional scrutiny, as the brief
    specifies.
51. As a VP, I want any fatal flag to reject the invoice automatically, so that clearly bad
    invoices never occupy my time.
52. As a VP, I want an invoice with no fatal flags, a low risk score, and a total under the
    threshold approved automatically, so that routine invoices clear without me.
53. As a VP, I want the LLM to reason about the invoice and the flags and produce written
    justification for the outcome, so that I can read why rather than infer it.
54. As a VP, I want an **approval critic** to attack the decision and surface what it missed,
    so that the reasoning has been challenged before it reaches me.
55. As a controller, I want the LLM able to escalate an invoice the rules would have approved,
    so that judgement can catch what no rule anticipated.
56. As a controller, I want the LLM unable to approve anything the rules did not approve, so
    that no document's text can talk its way into payment (ADR-0004).
57. As a controller, I want urgency and pressure language treated as a fraud signal rather
    than as instruction, so that INV-1003's "Pay immediately" makes the invoice more
    suspicious, not less.
58. As a controller, I want a vendor-identity change noted (INV-1012's "formerly FastShip
    Ltd."), so that renamed payees get a look.
59. As a VP, I want every decision to record which rule or reasoning drove it, so that the
    audit trail explains itself.

### Escalation and human review

60. As a VP, I want escalated invoices to appear in a review queue, so that I have one place
    to look.
61. As a VP, I want each queued invoice to show the extracted data, corrections, flags, risk
    score, the system's recommendation, and its reasoning, so that I can decide without
    opening the source document.
62. As a VP, I want the original document viewable alongside the extraction, so that I can
    check the system's reading when something looks off.
63. As a VP, I want to approve or reject a queued invoice with a reason, so that my decision
    is recorded as part of the trail.
64. As a VP, I want an approved invoice to continue to payment from exactly where it paused,
    so that nothing is recomputed or lost between my click and the payment (ADR-0005).
65. As an AP clerk, I want the CLI to report an escalation and exit rather than wait for a
    human, so that a batch of twenty invoices never blocks on the first one needing review.
66. As an engineer, I want paused run state persisted, so that a review can happen hours later
    or after a restart.

### Payment

67. As an AP clerk, I want an approved invoice to trigger the mock payment function with
    vendor and amount, so that the workflow completes end to end.
68. As a controller, I want a payment recorded in the registry with its invoice number, amount,
    and timestamp, so that what was paid is queryable.
69. As a controller, I want a rejected invoice to log the rejection with its reasoning and
    never call payment, as the brief specifies.
70. As a controller, I want payment attempted at most once per invoice number even if the
    pipeline is re-run, so that idempotency is a property of the system rather than of
    operator care.

### Dashboard

71. As an AP clerk, I want a live pipeline view showing each invoice moving through the four
    stages, so that I can see progress rather than wait blindly.
72. As an AP clerk, I want the current stage, elapsed time, and outcome visible per invoice,
    so that I can spot a stuck or slow run.
73. As a VP, I want the review queue as its own view showing only what needs me, so that I am
    not wading through cleared invoices.
74. As a controller, I want a ledger view listing every processed invoice with its decision,
    flags, and correction count, so that I can audit a batch.
75. As a controller, I want to filter and sort the ledger by decision, vendor, amount, and
    flag severity, so that I can find the cases I care about.
76. As a controller, I want a business-impact strip showing invoices processed, average
    processing time against the 5-day manual baseline, errors caught, and dollars flagged
    before payment, so that the system's value is stated in the terms the business uses.
77. As an engineer, I want a run drill-down showing every stage, every LLM call with its
    latency and token count, and the full reasoning, so that I can debug a specific run.
78. As an engineer, I want the drill-down to read persisted trace data rather than re-run the
    pipeline, so that inspecting a run is cheap and shows what actually happened.
79. As an AP clerk, I want the dashboard to update live as processing proceeds, so that the
    pipeline view is genuinely live rather than a refresh button.
80. As a first-time user, I want the dashboard to start with one Python command and no Node
    toolchain, so that running the system does not require a front-end build (ADR-0008).

### Observability and operation

81. As an engineer, I want every log line structured and tagged with a **run id**, so that one
    invoice's journey can be isolated from a concurrent batch.
82. As an engineer, I want every LLM call logged with prompt, response, latency, and token
    count, so that behaviour and cost are both inspectable.
83. As a controller, I want per-invoice cost reported, so that automated processing cost can
    be compared against the manual baseline.
84. As an engineer, I want identical LLM requests served from a content-hash cache, so that
    re-running the same batch during development costs nothing after the first pass.
85. As an engineer, I want an unexpected exception in any stage to fail that invoice with a
    recorded error and continue the batch, so that one bad document cannot abort a run.
86. As an engineer, I want the LLM provider selected by flag, defaulting to the real provider
    when a key is present and the fake one otherwise, so that the system runs with or without
    credentials (ADR-0001).
87. As a first-time user, I want the documented command to work verbatim, so that the first
    thing I try succeeds.
88. As a reviewer, I want `pytest` green on a clean clone with no API key, so that correctness
    is demonstrable without credentials.

### Setup

89. As a first-time user, I want the inventory database created by a documented command with
    seed data covering every item the sample invoices reference, so that validation works on
    first run.
90. As a first-time user, I want setup to be one install command with no compiler required, so
    that dependencies are not a barrier (ADR-0003).
91. As a developer, I want the catalogue seed to include expected unit prices and known
    vendors, so that price and vendor validation have ground truth.

## Implementation Decisions

### Module structure

Seven modules, each with a narrow interface:

- **documents** — format detection and routing. Structured formats (JSON, CSV, XML) are parsed
  deterministically here with no provider call, per ADR-0009; unstructured input (text, PDF,
  email body) is reduced to raw text for the extraction module. Owns the PDF path and is the
  only module that touches `pdfplumber`. Raises a typed extraction failure when a PDF has no
  text layer, and falls back to model extraction when a structured document fails to parse.
- **extraction** — raw text to a structured invoice, for documents the deterministic path did
  not handle. Owns the LLM extraction prompt, the Pydantic invoice schema used as the tool
  definition, the repair helpers (amount, date, item name), and the extraction critique loop.
  Emits corrections alongside the invoice. Both ingestion paths converge on one invoice model,
  so no later stage knows which ran.
- **catalogue** — the SQLite inventory: items with stock and expected unit price, plus known
  vendors. Read-only from the pipeline's perspective; a separate seeding entry point creates
  and populates it.
- **validation** — invoice plus catalogue to a list of flags. Pure, no LLM, no I/O beyond the
  catalogue reads passed in. Owns item normalisation and matching, stock aggregation, price
  comparison, date sanity, and risk scoring.
- **reconciliation** — the registry and duplicate/revision logic. Owns invoice identity,
  the four reconciliation outcomes, and the payment-idempotency guarantee.
- **approval** — deterministic rule evaluation, the LLM reasoning call, the approval critique
  loop, and the caution ratchet that constrains how the LLM may move the outcome.
- **payment** — wraps the mock payment function and records to the registry, whose ledger
  carries a uniqueness constraint on invoice identity, so double payment is impossible at the
  storage layer rather than merely prevented by application logic.

Plus three at the edges: **graph** (LangGraph wiring, state model, checkpointer), **cli**, and
**web** (FastAPI plus the served React bundle).

### The seams

Confirmed with the developer before writing this spec:

- **`run_invoice(document, deps) -> RunResult`** is the primary seam. The whole graph behind
  one call, returning decision, flags, corrections, and payment outcome. Behavioural
  assertions live here.
- **`Deps`** is a single container holding provider, catalogue, payment, clock, and registry.
  Every non-deterministic dependency enters through it; one container rather than five
  parameters so that adding a sixth dependency touches one type. Not itself a test target.
- **Pure core functions** (normalisation, repair, validation rules, risk scoring,
  reconciliation) are called directly in tests. No injection needed.
- **HTTP** via FastAPI's TestClient for the dashboard API, which delegates to the primary
  seam.

Rule: behaviour is asserted at the primary seam; the pure-function tests cover mechanical
string and number handling only. Tests do not attach to module internals.

### State and the graph

One Pydantic state object flows through every node, accumulating: the raw document, the
extracted invoice, corrections, flags, risk score, the decision with reasoning, critique
history, and the payment record. Nodes are plain functions over this state.

Edges: ingest to validate unconditionally; validate branches conditionally (fatal flag goes
straight to reject; otherwise approve); approve loops back on itself while the critic
demands revision, up to the retry cap; approve branches to pay, reject, or interrupt for
escalation. Backward edges implement the critique loops.

The SQLite checkpointer persists state after every node. This backs three things at once: run
traces for the drill-down, interrupt-and-resume for escalation, and the dashboard's history.

The architecture diagram in the README is generated from the live graph via
`draw_mermaid()` rather than drawn by hand, so it cannot drift.

### Flag severity and risk

Three severities with defined pipeline effects: **fatal** rejects and never reaches payment;
**soft** does not block but adds weight to the risk score; **info** is recorded only. Soft
flag weights and the escalation threshold live in config, not in code, so the policy is
visible and adjustable without touching logic.

Weights are set so that INV-1008 — unknown vendor, two uncatalogued items, emailed body,
$9,900 against a $10,000 threshold — crosses the escalation line, while INV-1010 — a
documented rush premium on an otherwise clean invoice — does not. A documented premium or
discount carries less weight than an undocumented one.

### Approval policy

Rules compute the outcome; the LLM may only move it toward more caution. Approve may become
escalate; escalate may become reject; reject is terminal. The LLM cannot approve what the
rules did not. This is a security boundary as much as a correctness one — invoice text is
untrusted input that sometimes argues with its reader.

One consequence follows directly: a fatally flagged invoice skips the critique loop, because
the ratchet forbids the model from downgrading a rejection, so the call cannot change the
outcome. Reasoning for the rejection is still recorded, as the brief requires.

### Item matching

Normalise (lowercase, strip non-alphanumerics), then exact match. A miss stays unknown; a
fuzzy pass runs only to name the nearest catalogue entry for the flag text, and never
influences matching. Measured on the sample data: normalisation resolves every legitimate
variant at ratio 1.000, and `WidgetC` scores 0.857 against a real item — close enough that a
conventional threshold would match it wrongly.

### Identity and reconciliation

Invoice identity is the invoice number, normalised so that `1002` and `INV-1002` are the same
identity, with vendor as a tiebreak when a number is absent. The registry maps identity to
processing state, decision, and payment record.

On a second document for a known identity: compare extracted fields. Identical means drop
with an info note. One a superset of the other means merge preferring the richer source, with
a soft flag naming the missing fields. A genuine contradiction on a shared field means a hard
flag and escalation showing both values. An explicit revision field means supersede, and if
the original was already paid, raise an exception naming the delta.

### Currency

Currency is captured explicitly, defaulting to USD only when the document is silent. A config
FX table converts to USD before any threshold comparison, and the conversion is recorded as a
correction naming the rate. Non-USD carries a soft flag.

### Provider and cost

A `Provider` interface with `grok` (the `openai` SDK against `api.x.ai/v1`) and `fake`
(replaying recorded cassettes) implementations, selected by `--provider`, defaulting to
`grok` when `XAI_API_KEY` is set. One model throughout, named in config. A content-hash cache
keyed on the full request means repeated development runs cost nothing after the first.

Token count, latency, and computed cost are recorded per call and aggregated per invoice.

### Interfaces

CLI: `python main.py --invoice_path=<file>` exactly as the brief specifies, plus
`--invoice_dir` for batch, `--provider`, and `--json` for machine-readable output. Batch is
sequential; concurrency is a config value rather than a rewrite, and the registry race that
concurrency would introduce is documented rather than solved.

Web: FastAPI serving a committed React bundle as static files, with a JSON API and
server-sent events for live updates. Endpoints cover listing runs, fetching one run's full
trace, listing the queue, and submitting a human decision that resumes a paused run.

### Error handling

Typed failures per stage: extraction failure, validation failure, provider failure. An
unexpected exception fails that invoice with the error recorded in its run and the batch
continues. Every failure path is a recorded outcome, never a stack trace to stderr.

## Testing Decisions

### What makes a good test here

A good test asserts what a user or an auditor would notice: the decision an invoice received,
the flags raised, the corrections recorded, whether payment was called and with what. It does
not assert which function produced that outcome, how many LLM calls happened, or what the
graph's internal state looked like mid-run.

The concrete standard: **it must be possible to rewrite any single module — the CSV parser,
the extraction prompt, even the orchestration framework — without editing a behavioural
test.** A test that breaks under refactoring while behaviour is unchanged is a bug in the
test.

Three layers, per ADR-0006:

1. **Unit, no LLM.** Pure functions called directly: amount repair, date normalisation, item
   normalisation and matching, validation rules, risk scoring, reconciliation outcomes, FX
   conversion, identity normalisation. Milliseconds, and the bulk of the suite by count.
2. **Recorded.** Real provider request/response pairs captured once to cassettes and replayed
   offline. These exercise the full graph through the primary seam against real model output.
   Deterministic, free, and no key needed. The `fake` provider and the cassette replay share
   one implementation.
3. **Evals, scored not asserted.** Per-field extraction accuracy and decision agreement,
   reported as percentages so that a prompt change surfaces as "94% to 81%" rather than one
   opaque failure. Scored expectations cover only outcomes specified externally — the four
   scenarios the brief states, and the authored fixtures whose ground truth is known by
   construction. Expectations invented for the remaining provided invoices would measure
   agreement with ourselves rather than correctness, so those invoices are processed and
   reported but not scored, and the report distinguishes the two so coverage is never
   overstated. Where a structured document was parsed deterministically its fields are genuine
   ground truth, and the model path can be scored against them.

A live smoke test runs only when `XAI_API_KEY` is present and skips otherwise.

### What gets tested where

Through the **primary seam**, one behavioural test per interesting invoice:

- INV-1001 — clean invoice, approved, paid.
- INV-1002 — stock exceeded, typo'd labels repaired, due date not after invoice date.
- INV-1003 — zero-stock item, unresolvable due date, urgency language; rejected, and the
  urgency did not influence the outcome favourably.
- INV-1004 plus its revision — revision supersedes; $5,940 paid once, not $7,830.
- INV-1008 — passes every individual rule, escalates on risk score.
- INV-1009 — negative quantity, empty vendor; fatally rejected, payment never called.
- INV-1011 — PDF and TXT twins reconciled; richer source preferred, gap soft-flagged, paid
  once.
- INV-1012 — OCR corruption repaired with corrections recorded; vendor rename noted.
- INV-1013 — eight line items across three prices, WidgetA aggregating to 22 against 15 in
  stock; over threshold.
- INV-1014 — EUR converted before threshold comparison, conversion recorded.
- INV-1016 — WidgetC unknown, not matched to WidgetA, near-match named in the flag only.

Plus the four authored fixtures in `data/invoices_extra/`: a padded-price invoice that only
the price check catches, a threshold-evasion pair at $9,999 and $10,001, an image-only PDF
that must fail cleanly, and a duplicate whose total contradicts the original.

Through the **HTTP seam**: an escalated run appears in the queue; approving it resumes the
graph from the interrupt and reaches payment; rejecting it records the reason and does not
pay; the run detail endpoint returns a persisted trace.

Idempotency gets its own test: processing the same directory twice produces the same payment
count, not double.

### Prior art

None — this is a greenfield repo, so this spec establishes the conventions rather than
following them. The layering, the single-container dependency injection, and the
cassette approach are the patterns later work should copy.

## Out of Scope

- **Real integrations.** Payment, inventory, and approval routing are mocked per the brief.
  No real banking API, no email sending, no OCR service.
- **Vision extraction, deferred rather than refused.** All provided PDFs carry text layers, so
  nothing in the dataset requires reading pixels. A multimodal pass over a rendered page would
  turn the authored image-only fixture from a graceful failure into a processed invoice; it is
  deferred until the core pipeline is complete rather than ruled out.
- **Purchase-order matching.** Three-way matching against purchase orders is the stronger
  real-world control, but exactly one document in the dataset cites a purchase order, so any
  records to match against would be invented. The reference is captured and flagged as
  uncheckable instead, which states the limitation without fabricating data.
- **Authentication and multi-tenancy.** The dashboard has no login. Roles shape the views,
  not permissions.
- **Concurrent batch processing.** Sequential by decision. The registry race concurrency
  would introduce is documented, not solved.
- **Checkpoint migration.** A state-schema change may invalidate existing checkpoints.
  Acceptable for a prototype; named as a production concern.
- **Model tiering.** One model throughout. Per-agent model selection is a real optimisation
  but doubles the config surface for a twenty-invoice demo.
- **Live provider monitoring.** No scheduled canary, no drift detection.
- **Deployment.** Local only, per the brief.
- **Vendor master-data management.** The known-vendors table is seed data, not a maintained
  record with an onboarding flow.

## Further Notes

**Two controls are not in the brief.** Duplicate and revision detection is absent from the
requirements, but the sample data cannot be handled correctly without it: INV-1011 and
INV-1013 each arrive as two documents, and INV-1004 has a revision that raises it by $4,050.
Processing documents independently pays $7,830 against a $5,940 obligation. Likewise, nothing
in the brief asks for risk-score compounding, but no single stated rule stops INV-1008.
Both are treated as requirements here because the data demands them, not because they were
requested.

**Invoice text is untrusted input.** Several documents contain instructions aimed at whoever
reads them — INV-1003 presses for immediate payment by wire. Any design that lets document
text influence the approval outcome directly is an injection path, which is why the caution
ratchet is a security boundary rather than a preference.

**Release checks.** Two failure modes are silent and severe. A committed React bundle can go
stale against its source, shipping an interface that does not match the code. Recorded
cassettes can drift out of sync with changed prompts, so the suite passes against responses
the current prompts would never produce. Both need an explicit check before any release, not
a habit.
