# ADR-0001: The LLM is the only network dependency

- **Status:** Accepted
- **Date:** 2026-08-22

## Context

The brief contains two instructions that cannot both be followed literally. It requires
xAI's Grok as "the core reasoning engine (via the xAI API)", and it also requires the
runtime to "assume no internet for external APIs — simulate everything locally."

The brief resolves its own contradiction by what it supplies: mocks for the inventory
database (SQLite), the payment API (`mock_payment`), and VP approval ("simulate VP-level
review"). It supplies no mock LLM.

A second problem: the brief's snippet (`from xai import Grok`, host `grok.x.ai`) is
pseudocode. There is no such package. xAI's API is OpenAI-compatible, reached with the
`openai` SDK against `https://api.x.ai/v1`.

## Decision

Grok is the single real network call. Every Acme-side system — inventory, payment, approval
routing — is mocked locally.

Access it through a narrow `Provider` interface with three implementations:

- `grok` — the `openai` SDK pointed at `api.x.ai/v1` (default when a key is present)
- `fake` — replays recorded responses from `tests/cassettes/`, requires no key
- future providers (Anthropic, OpenAI) are a base-URL change, not a rewrite

Selected by `--provider`, defaulting to `grok` when `XAI_API_KEY` is set and `fake`
otherwise.

## Consequences

**Good.** A grader with no API key clones the repo and gets a working end-to-end system
against real recorded model output — functionality is never scored on a missing credential.
The interface also makes the test strategy in ADR-0006 possible at all.

**Bad.** Two code paths to keep honest; recorded responses go stale when prompts change,
which is why regenerating them is an explicit documented step rather than a hidden one.

**Accepted risk.** The exact model id (`grok-3` vs a newer release) is verified against xAI's
docs once a key exists, and lives in config rather than in code.
