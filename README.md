# Invoice Processing Automation

Acme Corp processes supplier invoices by hand: a clerk reads the document, checks items
against inventory, chases a VP for approval by email, then pays. It costs **$2M/year**,
carries a **30% error rate**, and takes **5 days** per invoice.

This is a multi-agent system that runs that whole workflow instead — **ingestion**
(document in, structured invoice out), **validation** (against the inventory catalogue),
**approval** (deterministic rules plus an LLM that may only add caution, never overrule
one), and **payment** (mocked, idempotent) — with every judgment call recorded as an
auditable correction, flag, or decision rather than a silent guess. `CONTEXT.md` has the
full vocabulary; the numbers above are what every feature here is measured against.

## Install

```bash
python -m venv .venv && .venv/Scripts/activate    # or: source .venv/bin/activate
pip install -e ".[dev]"
```

No API key needed. Without one the system uses a fake reasoning provider that replays
recorded responses, so a clean clone runs the tests and the CLI as-is
([ADR-0001](docs/adr/0001-llm-is-the-only-network-dependency.md)). Copy `.env.example` to
`.env` and set `XAI_API_KEY` to use Grok for real (then export it into your shell — nothing
loads `.env` automatically).

```bash
pytest          # no network, no credentials — green on a clean clone
mypy            # strict, src and tests
```

## Catalogue seed

The inventory catalogue (SQLite: item, stock, expected price, known vendors) creates and
seeds itself on first run. To reset it deliberately:

```bash
python main.py --seed-catalogue
```

## Single-invoice run

```bash
python main.py --invoice_path=data/invoices/invoice_1001.txt
python main.py --invoice_path=data/invoices/invoice_1013.json   # aggregated stock exceeded: rejected
```

An escalated invoice pauses rather than failing — the run reports the document name to
resume with:

```bash
python main.py --invoice_path=data/invoices_extra/invoice_9003_threshold_over.json   # over $10K: escalated
python main.py --resume=invoice_9003_threshold_over.json --decision=approved --reason="VP confirmed by phone"
```

## Batch run

```bash
python main.py --invoice_dir=data/invoices
```

One bad document never stops the rest; a summary is reported at the end (approved,
rejected, escalated, failed counts). This is the same code path
[the eval harness](docs/adr/0006-recorded-responses-for-tests.md) drives over the golden set.

Without a key, the deterministic (JSON/CSV/XML) documents process normally; the text and
PDF documents report `failed` — the fake provider only ships pre-recorded answers for the
two documents the quickstart above uses ([ADR-0009](docs/adr/0009-deterministic-parsing-for-structured-formats.md)).
`python scripts/record_cassettes.py` plus a real `XAI_API_KEY`, or `--provider grok`, gets
every document a real answer.

## Dashboard

```bash
python -m invoice_automation.web    # http://127.0.0.1:8000
```

Pipeline view, ledger, and the review queue for escalated invoices. No Node toolchain
required to run it — the React/Vite production bundle is committed under
`src/invoice_automation/static/` and served as static files
([ADR-0008](docs/adr/0008-react-with-committed-bundle.md)). To rebuild it after changing
the front end:

```bash
python scripts/rebuild_dashboard.py    # npm install && npm run build, then stamps the bundle
```

`scripts/check_bundle_fresh.py` (run in `pytest`) fails if the committed bundle and
`frontend/src` disagree — a source change with no matching rebuild is caught rather than
shipped silently stale.

## Docker

No local Python (or Node) installation needed at all:

```bash
docker build -t invoice-automation .
docker run -p 8000:8000 invoice-automation                          # dashboard
docker run invoice-automation python main.py --invoice_dir=data/invoices   # CLI, one-off
```

Runs with no API key (the fake provider) exactly like the local path above; pass
`-e XAI_API_KEY=...` to use Grok for real. The documented local path (`pip install -e
".[dev]"`, no Docker) still works unchanged — this is an alternative, not a replacement.

## Commit messages

`git config core.hooksPath .githooks` (one-time, local to this clone) turns on a
`commit-msg` hook that rejects a commit whose subject isn't Conventional Commits
(`type(scope): subject`, type one of `feat|fix|refactor|test|docs|chore|perf|build|ci`)
or is a placeholder like `wip`/`fix stuff`. `git config commit.template .gitmessage`
makes `git commit` (no `-m`) open with the format and a reminder to write *why*, not
*what* — the diff already shows what.

## Where things live

| Path | What it is |
| ---- | ---------- |
| [REQUIREMENTS.md](REQUIREMENTS.md) | The original brief, unmodified |
| [docs/SPEC.md](docs/SPEC.md) | The spec — problem, 91 user stories, scope boundaries |
| [CONTEXT.md](CONTEXT.md) | Domain glossary — the vocabulary this codebase speaks |
| [docs/adr/](docs/adr/) | Architecture decisions, each with its cost stated |
| [data/invoices/](data/invoices/) | The 16 provided sample invoices, untouched |
| [data/invoices_extra/](data/invoices_extra/) | 6 authored fixtures reaching scenarios the provided data doesn't |

## Design in one table

| Area | Decision | Why |
| ---- | -------- | --- |
| Reasoning engine | Grok via xAI's OpenAI-compatible API, behind a provider interface | The only real network call; everything Acme-side is mocked ([ADR-0001](docs/adr/0001-llm-is-the-only-network-dependency.md)) |
| Ingestion | Structured formats parsed deterministically; the model reads only what needs judgment | A JSON invoice is already extracted; sending it to an LLM adds cost and a hallucination risk ([ADR-0009](docs/adr/0009-deterministic-parsing-for-structured-formats.md)) |
| Orchestration | LangGraph — nodes, conditional edges, cycles, checkpointer | The workflow is a cyclic graph, not a pipeline ([ADR-0002](docs/adr/0002-langgraph-for-orchestration.md)) |
| Agents | Four stage agents plus one shared Critic | Critique loops in ingestion and approval |
| Bad data | Auditable corrections: raw, value, reason, confidence | A 30% error rate is not fixed by being confidently wrong more quietly |
| Approval | Deterministic rules, LLM may only add caution | Invoice text is untrusted input ([ADR-0004](docs/adr/0004-caution-ratchet-for-approval.md)) |
| Duplicates | Registry keyed on invoice number; revisions supersede | INV-1011 arrives twice; INV-1004 has a revision worth $4,050 |
| Escalation | Graph interrupts, checkpoints, resumes on human action | Real human-in-the-loop, not a mock of one ([ADR-0005](docs/adr/0005-interrupt-and-resume-for-escalation.md)) |
| Interface | CLI (as specified) plus a React dashboard served by FastAPI | Pipeline, review queue, ledger ([ADR-0008](docs/adr/0008-react-with-committed-bundle.md)) |
| Tests | Unit, recorded cassettes, evals | Green on a clean clone with no API key ([ADR-0006](docs/adr/0006-recorded-responses-for-tests.md)) |

## Architecture

Generated from the live `StateGraph` (`graph.build_graph`) by
`python scripts/generate_diagram.py` — this is the actual graph `run_invoice` executes,
not a drawing of it, so it cannot drift from the code.

<!-- ARCHITECTURE_DIAGRAM:START -->

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	ingest(ingest)
	reconcile(reconcile)
	validate(validate)
	approve(approve)
	await_review(await_review)
	pay(pay)
	__end__([<p>__end__</p>]):::last
	__start__ --> ingest;
	approve -.-> __end__;
	approve -.-> await_review;
	approve -.-> pay;
	await_review -.-> __end__;
	await_review -.-> pay;
	ingest --> reconcile;
	reconcile --> validate;
	validate --> approve;
	pay --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

<!-- ARCHITECTURE_DIAGRAM:END -->

## Tests, in three layers

Per [ADR-0006](docs/adr/0006-recorded-responses-for-tests.md):

1. **Unit** — parsers, validation, reconciliation, FX, no LLM: the bulk of the suite.
2. **Recorded** — `tests/cassettes/`, real Grok request/response pairs captured once and
   replayed offline (`tests/test_cassettes.py`). Recording is one documented command,
   because it needs a key:
   ```bash
   python scripts/record_cassettes.py
   ```
   `FakeProvider` (no key needed, ever) and cassette replay share one implementation —
   recording just points the same loader at `tests/cassettes/` instead of the package's
   bundled sample responses. `scripts/check_cassettes_fresh.py` (also run in `pytest`)
   catches a cassette recorded against a prompt or schema that has since changed.
3. **Evals** — `python -m invoice_automation.evals` scores the golden set (16 provided
   invoices plus the authored fixtures) on decision agreement and field accuracy, so a
   prompt change reports "94% to 81%" instead of one opaque red test.

The test suite never calls the real Grok API — it runs entirely against `FakeProvider`/cassette
replay regardless of whether `XAI_API_KEY` is set. To check the live integration, run the CLI
manually with `--provider grok` and a real key (see above).
