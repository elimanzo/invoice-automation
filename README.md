# Invoice Processing Automation

A multi-agent system that takes a supplier invoice from a messy document to a paid (or
rejected, or escalated) obligation — ingestion, validation against inventory, rule-plus-LLM
approval, and payment.

> **Status: design complete, implementation in progress.** This README is a placeholder and
> gets replaced with setup instructions, the generated architecture diagram, and a demo
> walkthrough once the system runs.

## The problem

Acme Corp processes supplier invoices by hand: read the document, check items against
inventory, chase a VP by email, pay. It costs **$2M/year**, carries a **30% error rate**, and
takes **5 days** per invoice. Those three numbers are what this system is measured against.

## Where things live

| Path | What it is |
| ---- | ---------- |
| [REQUIREMENTS.md](REQUIREMENTS.md) | The original brief, unmodified |
| [CONTEXT.md](CONTEXT.md) | Domain glossary — the vocabulary this codebase speaks |
| [docs/adr/](docs/adr/) | Architecture decisions, each with its cost stated |
| [data/invoices/](data/invoices/) | The 16 provided sample invoices, untouched |

## Design in one table

| Area | Decision | Why |
| ---- | -------- | --- |
| Reasoning engine | Grok via xAI's OpenAI-compatible API, behind a provider interface | The only real network call; everything Acme-side is mocked ([ADR-0001](docs/adr/0001-llm-is-the-only-network-dependency.md)) |
| Orchestration | LangGraph — nodes, conditional edges, cycles, checkpointer | The workflow is a cyclic graph, not a pipeline ([ADR-0002](docs/adr/0002-langgraph-for-orchestration.md)) |
| Agents | Four stage agents plus one shared Critic | Critique loops in ingestion and approval |
| Bad data | Auditable corrections: raw, value, reason, confidence | A 30% error rate is not fixed by being confidently wrong more quietly |
| Approval | Deterministic rules, LLM may only add caution | Invoice text is untrusted input ([ADR-0004](docs/adr/0004-caution-ratchet-for-approval.md)) |
| Duplicates | Registry keyed on invoice number; revisions supersede | INV-1011 arrives twice; INV-1004 has a revision worth $4,050 |
| Escalation | Graph interrupts, checkpoints, resumes on human action | Real human-in-the-loop, not a mock of one ([ADR-0005](docs/adr/0005-interrupt-and-resume-for-escalation.md)) |
| Interface | CLI (as specified) plus a React dashboard served by FastAPI | Pipeline, review queue, ledger ([ADR-0008](docs/adr/0008-react-with-committed-bundle.md)) |
| Tests | Unit, recorded cassettes, evals | Green on a clean clone with no API key ([ADR-0006](docs/adr/0006-recorded-responses-for-tests.md)) |
