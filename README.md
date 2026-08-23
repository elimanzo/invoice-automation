# Invoice Processing Automation

A multi-agent system that takes a supplier invoice from a messy document to a paid (or
rejected, or escalated) obligation — ingestion, validation against inventory, rule-plus-LLM
approval, and payment.

> **Status: in progress.** Ticket 01 of 18 is complete — a document becomes a structured
> invoice. Validation, approval, payment, and the dashboard are still to come, so this README
> gets replaced with the full walkthrough and generated architecture diagram once the pipeline
> runs end to end.

## Quickstart

```bash
python -m venv .venv && .venv/Scripts/activate    # or: source .venv/bin/activate
pip install -e ".[dev]"
python main.py --invoice_path=data/invoices/invoice_1001.txt
```

No API key needed. Without one the system uses a fake reasoning provider that replays
recorded responses, so a clean clone runs and the test suite passes as-is
([ADR-0001](docs/adr/0001-llm-is-the-only-network-dependency.md)). Copy `.env.example` to
`.env` and set `XAI_API_KEY` to use Grok for real.

The inventory catalogue creates and seeds itself on first run. `python main.py
--seed-catalogue` resets it deliberately.

```bash
pytest          # 17 tests, no network, no credentials
mypy            # strict, src and tests
```

## The problem

Acme Corp processes supplier invoices by hand: read the document, check items against
inventory, chase a VP by email, pay. It costs **$2M/year**, carries a **30% error rate**, and
takes **5 days** per invoice. Those three numbers are what this system is measured against.

## Where things live

| Path | What it is |
| ---- | ---------- |
| [REQUIREMENTS.md](REQUIREMENTS.md) | The original brief, unmodified |
| [docs/SPEC.md](docs/SPEC.md) | The spec — problem, 91 user stories, scope boundaries |
| [CONTEXT.md](CONTEXT.md) | Domain glossary — the vocabulary this codebase speaks |
| [docs/adr/](docs/adr/) | Architecture decisions, each with its cost stated |
| [data/invoices/](data/invoices/) | The 16 provided sample invoices, untouched |

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
