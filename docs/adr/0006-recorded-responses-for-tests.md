# ADR-0006: Three test layers, with recorded LLM responses in the middle

- **Status:** Accepted
- **Date:** 2026-08-22

## Context

Most of the system's behaviour depends on LLM output, which is slow, costs money per call,
and is not deterministic. A test suite that hits the live API on every run is unusable; a
suite built on hand-written mock responses tests our imagination of the model rather than
the model.

## Decision

Three layers.

1. **Unit — no LLM.** Parsers, validation rules, reconciliation, FX, item normalisation.
   Pure functions, milliseconds, the bulk of the suite. For example,
   `parse_amount("$3,500.O0") == Decimal("3500.00")`.
2. **Recorded — cassettes.** Real Grok request/response pairs for the sample invoices,
   captured once to `tests/cassettes/` and replayed offline. Exercises the real graph
   against real model output at zero cost, deterministically. Shares its implementation with
   the `fake` provider from ADR-0001.
3. **Evals — scored, not asserted.** A golden set (the 16 provided invoices plus the four
   authored ones) scored on extraction field accuracy and decision agreement, so a prompt
   change reports "field accuracy 94% to 81%" instead of one opaque red test.

A live smoke test runs only when `XAI_API_KEY` is present, and is skipped otherwise.

## Consequences

**Good.** `pytest` is green on a clean clone with no API key, having exercised the real
pipeline. Prompt regressions are measurable rather than anecdotal.

**Bad.** Cassettes go stale when prompts change and must be regenerated — an explicit
documented step, and one that requires a key.

**Not done.** Production would add request tracing with per-call latency and token cost, and
a scheduled live canary to detect provider drift. Out of scope here; named so the omission
is a decision rather than an oversight.

**Ticket 18 scoping note.** Cassettes cover `structured()` — extraction and the extraction
critic — since that is what the "recorded" layer needs a real answer for. The approval
agent's tool-calling conversation (`converse()`, ticket 08) is not cassette-replayed: it has
no per-document key in its own signature (it only ever sees `messages`/`tools`), and every
existing test since ticket 08 already relies on `FakeProvider.converse()`'s fixed "no further
concerns" default rather than a scripted one. Recording real conversations would need a
larger interface change for a stage that, by the caution ratchet (ADR-0004), can only ever
tighten a decision the deterministic rules already reached — not silently changing it.
Two silent-staleness checks close the loop this ADR leaves open: `scripts/check_cassettes_fresh.py`
fingerprints every request the current code builds and compares it against the fingerprint
recorded alongside each cassette, and `scripts/check_bundle_fresh.py` does the same for the
committed dashboard bundle (ADR-0008) against `frontend/src`.
