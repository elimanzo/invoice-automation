# ADR-0009: Structured formats are parsed deterministically, not by the model

- **Status:** Accepted
- **Date:** 2026-08-22

## Context

The sample data arrives in five formats. Two of them — plain text and PDF — are unstructured
prose and tables where field boundaries must be inferred. Three — JSON, CSV, XML — are already
structured: the fields are named, the values are typed, and the document states exactly which
quantity belongs to which item.

The initial design sent every document through the same model-based extraction path. That is
straightforwardly wrong for the structured three. A JSON invoice is already extracted;
`json.load` reads it perfectly, instantly, and free. Sending it to a language model is slower,
costs money per invoice, and introduces a failure mode that did not previously exist — a model
can misread a field that a parser cannot.

The counter-argument is that structured input is not necessarily *correct* input. One provided
invoice parses without complaint and is still nonsense: empty vendor name, null due date,
negative quantity, and a total that contradicts its own line items.

## Decision

Route by format. JSON, CSV, and XML are parsed deterministically with no provider call. Text,
PDF, and email bodies go through model extraction. Both paths produce the same invoice model, so
nothing downstream knows or cares which ran. A structured document that fails to parse falls back
to model extraction rather than failing outright.

Deterministic parsing extracts fields only. It makes no judgment about whether those fields make
sense — that stays in validation, where it belongs, so a well-formed document carrying absurd
values is still caught.

Because the true values of a structured document are knowable, the two paths also form a
cross-check: running model extraction over a document whose fields are already known measures
extraction accuracy against ground truth rather than against opinion. Field-level disagreement is
recorded as a flag. This runs as an explicit mode, not on every invoice.

## Consequences

**Good.** Roughly half the sample invoices process with zero provider calls — faster, free, and
immune to hallucination. Extraction accuracy becomes measurable against real ground truth
instead of against expectations we authored.

**Good.** The failure surface shrinks. A misparsed CSV is a bug with a stack trace and a
reproducing test; a misread CSV is a probabilistic error that may not recur.

**Bad.** Two ingestion paths to maintain and test rather than one, and the fallback edge — a
structured document that fails to parse — is a third path that is easy to leave untested.

**Consequence for the critique loop.** The extraction critic only has work to do on the model
path. Structured documents skip it, which is correct but means the loop gets less exercise from
the sample data than it otherwise would.

**Not a reduction in scope.** The reasoning engine still handles every genuinely ambiguous
document, plus all judgment about validity, approval, and fraud. This decision moves mechanical
field-reading off the model; it does not move reasoning off it.
