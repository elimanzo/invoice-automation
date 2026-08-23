# Architecture Decision Records

Numbered, immutable decisions. Each states the context that forced a choice, the choice
made, and what it costs. Superseded decisions get marked, not deleted.

| # | Decision |
| ---- | -------- |
| [0001](0001-llm-is-the-only-network-dependency.md) | The LLM is the only network dependency |
| [0002](0002-langgraph-for-orchestration.md) | LangGraph for orchestration |
| [0003](0003-python-314-floor-311.md) | Target Python 3.14, declare a 3.11 floor |
| [0004](0004-caution-ratchet-for-approval.md) | Approval is a caution ratchet |
| [0005](0005-interrupt-and-resume-for-escalation.md) | Escalation interrupts the graph and resumes on human action |
| [0006](0006-recorded-responses-for-tests.md) | Three test layers, with recorded LLM responses in the middle |
| [0007](0007-fuzzy-matching-suggests-never-substitutes.md) | Fuzzy matching may suggest, never substitute |
| [0008](0008-react-with-committed-bundle.md) | React front end with a committed bundle |
| [0009](0009-deterministic-parsing-for-structured-formats.md) | Structured formats are parsed deterministically, not by the model |
| [0010](0010-redundant-critics-considered-deferred.md) | Redundant independent critics for escalation (considered, deferred) |
| [0011](0011-uploaded-originals-retained-permanently.md) | Uploaded invoice originals are retained permanently |
| [0012](0012-reviewer-edits-via-checkpoint-update.md) | Reviewer edits reach the invoice via checkpoint update, not graph rewind |

Domain vocabulary lives in [CONTEXT.md](../../CONTEXT.md). The original brief is
[REQUIREMENTS.md](../../REQUIREMENTS.md).
