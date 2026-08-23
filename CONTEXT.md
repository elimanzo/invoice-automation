# Context: Invoice Processing Automation

The domain glossary for this project. Use these terms exactly; do not drift to synonyms
listed under "Avoid".

Source of the problem statement: [REQUIREMENTS.md](REQUIREMENTS.md) (the original brief).
Decisions with trade-offs are recorded in [docs/adr/](docs/adr/).

## The business problem

Acme Corp processes supplier invoices by hand: a clerk reads the document, checks items
against inventory, chases a VP for approval by email, then pays. It costs **$2M/year**,
carries a **30% error rate**, and takes **5 days** per invoice. Those three numbers are the
baseline every feature here is measured against.

## Core terms

### Invoice
A payment demand from a **vendor**, identified by its **invoice number**. One invoice is one
obligation — it may be paid **at most once**, no matter how many **documents** carry it.

Avoid: "bill", "receipt". A receipt records a payment already made; an invoice requests one.

### Document
A single file that carries an invoice: `.txt`, `.json`, `.csv`, `.xml`, or `.pdf`. One
invoice may arrive as several documents (INV-1011 arrives as both PDF and TXT), and those
documents may disagree. Document is the *input*; invoice is the *thing*.

### Line item
One row of an invoice: item name, quantity, unit price. An invoice has one or more. The same
item may appear on several line items at different prices — INV-1013 bills WidgetA three
times at $250, $240, and $250 — so line items are never silently merged.

### Item
A product in Acme's catalogue, e.g. `WidgetA`. Distinct items are distinct even when their
names are similar: `WidgetA` and `WidgetB` are different products, and `WidgetC` is not a
product at all.

Avoid: "SKU", "part". Same idea, but the data says "item".

### Catalogue / inventory
The SQLite database of items Acme stocks: name, stock on hand, expected unit price. Also
holds known **vendors**. This is the ground truth **validation** checks against.

### Correction
A record that the system stored something different from what a document literally said.
Carries `field`, `raw`, `value`, `reason`, `confidence`, `source`. Corrections are written for
every mutation, always — they are an audit trail, not an exception path, and they never
require a human. Low `confidence` triggers **escalation** rather than a guess.

`source` is `"model"` or `"human"` — a **reviewer** editing a field on an escalated invoice
also writes a `Correction`, never a silent overwrite, with `confidence` `1.0` (a human
assertion is maximally sure) and `source: "human"`. For a human correction, `raw` is the
value being overwritten — whatever was last stored for that field — not the original
document text; several corrections may already sit between the document and that value. See
ADR-0012.

Example: INV-1012 says `26-Jan-2O26`; the correction records the letter-O-for-zero
substitution at 0.95 confidence and stores `2026-01-26`.

Avoid: "fix", "cleanup". Those hide the fact that the original is retained.

### Flag
A finding about an invoice, carrying a **severity**:

- **fatal** — auto-reject; the invoice never reaches **payment**. (INV-1009's negative
  quantity; INV-1003's zero-stock FakeItem.)
- **soft** — does not block. Recorded, and contributes to the **risk score**. (INV-1010's
  rush-order price premium; INV-1014's non-USD currency.)
- **info** — recorded only. (INV-1013's volume-discount annotations.)

A soft flag is a vote, not a veto.

### Risk score
The accumulated weight of an invoice's soft flags. Past a threshold it forces
**escalation** even when no single flag is fatal. This is what catches INV-1008: unknown
vendor, unknown items, and a $9,900 total sitting just under the $10K scrutiny line — no
individual rule stops it.

### Decision
The outcome of the **approval** stage: `approved`, `rejected`, or `escalated`. Always
carries reasoning. Produced by deterministic rules, then examined by the **critic** under
the **caution ratchet** (see ADR-0004).

### Escalation
A decision to hand an invoice to a human. The graph **interrupts**, state is checkpointed,
and the run resumes from that exact point when a reviewer acts in the dashboard. An
escalated invoice is paused, not failed.

### Reconciliation
What happens when a second **document** arrives for an invoice already in the registry.
Outcomes: identical copy (drop), one copy richer (merge, prefer richer, soft-flag the gap),
copies contradict (hard-flag, escalate — never guess), or explicit **revision**.

### Revision
A document declaring itself a replacement for an earlier one, via an explicit `revision`
field. It supersedes the original. `invoice_1004_revised.json` raises INV-1004 from $1,890
to $5,940; processing both naively pays $7,830 against a $5,940 obligation.

Superseding is not the same as arriving second. When both documents are in one batch, the
pair is **contested** and neither pays until a human says which is current — filename order
is not a business rule. See ADR-0013.

### Contested submission
Two or more **documents** in one batch claiming the same invoice, at least one of them a
**revision**. Every document in the group is held for a **reviewer**; none pays
automatically. Detected before the batch's first run, from identities readable without the
model.

A plain duplicate is *not* contested — **reconciliation** resolves those whichever arrives
first. Contested means the *amount owed itself* is in dispute, not just which copy is
richer.

### Critic
The agent that attacks another agent's output. Used in two loops: after **ingestion** (does
the extraction match the raw text? do line items sum to the subtotal?) and inside
**approval** (what did this decision miss?). One role, two call sites.

Avoid: "reviewer" — that means the human in the queue.

### Clerk
The AP (accounts-payable) clerk — the human who starts a **run** by submitting a document,
via the CLI or the dashboard's upload action. A clerk feeds documents in; a clerk never
decides anything. Distinct from a **reviewer**, who only ever acts on an already-escalated
invoice.

Avoid: conflating with "reviewer" — a clerk and a reviewer can be the same person in real
life, but they are different roles at different stages here, and the system never assumes
one implies the other.

## The four stages

1. **Ingestion** — document in, structured invoice out, with **corrections**.
2. **Validation** — invoice against the **catalogue**, producing **flags**.
3. **Approval** — rules plus reasoning under the caution ratchet, producing a **decision**.
4. **Payment** — mocked; idempotent on invoice number.

## Terms that mean something specific here

- **Provider** — the LLM behind an interface. Grok is the default; a `fake` provider replays
  recorded responses so the system runs with no API key.
- **Run** — one invoice's journey through the graph, identified by a **run id** that appears
  in every log line and trace for it.
- **Registry** — the table mapping invoice number to processing state. What makes payment
  idempotent and duplicates detectable.
