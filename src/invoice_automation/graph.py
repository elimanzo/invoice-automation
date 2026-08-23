"""The graph: four stages, one state, one seam.

Per ADR-0002, LangGraph gives the workflow what a straight pipeline function could not —
conditional branching and cycles for later tickets, a checkpointer that persists state
after every node, and a diagram generated from the graph that actually runs. This ticket
wires the graph linearly (ingest, validate, approve, pay); branching, the critique loops,
and interrupt-and-resume arrive as the tickets that need them.

State is a plain, JSON-shaped dict rather than a Pydantic model directly. LangGraph's
checkpointer round-trips a TypedDict of primitives and dicts without needing any type
registered for serialization, and JSON-shaped state is also what tickets 12 and 14 will
read to render a persisted trace. Nodes convert to and from the domain models at their
boundary; nothing outside this module deals in raw dicts.

`run_invoice(document, deps)` is the primary seam every other ticket tests against. It is
deliberately the only function most callers need — everything else in this module is
plumbing it stands on.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from .approval import decide, run_approval_agent
from .deps import Deps
from .documents import Document
from .extraction import extract_invoice
from .models import Correction, Decision, DocumentFormat, Flag, Invoice, ToolCallRecord
from .payments import PaymentResult
from .registry import DuplicatePayment, normalize_invoice_identity
from .structured_parsing import StructuredParseFailed, parse_structured
from .validation import validate_invoice

_STRUCTURED_FORMATS = frozenset(
    {DocumentFormat.JSON, DocumentFormat.CSV, DocumentFormat.XML}
)


class PipelineState(TypedDict, total=False):
    """The graph's state. Every field is JSON-shaped, so the checkpointer needs no
    special handling and a persisted trace is directly readable."""

    document_path: str
    document_format: str
    document_name: str
    raw_text: str
    invoice: dict[str, Any] | None
    flags: list[dict[str, Any]]
    decision: dict[str, Any] | None
    payment: dict[str, Any] | None
    corrections: list[dict[str, Any]]
    extraction_method: str
    """Which path ingestion took: "deterministic" or "model" (ADR-0009). Recorded so a
    run's trace can show it without downstream code needing to care which one ran."""
    usd_total: str | None
    """The invoice total converted to USD (validation.py), for approval's dollar
    threshold. A string, like every other JSON-shaped state field — approval parses it
    back to Decimal at its own boundary, same as invoice and flags."""
    tool_calls: list[dict[str, Any]]
    """Every read-only tool call the approval agent made, in order, with its arguments
    and result — the investigation trace ticket 08 asks for."""


@dataclass(frozen=True)
class RunResult:
    """What `run_invoice` returns: the whole outcome, nothing hidden in a side channel."""

    invoice: Invoice | None
    flags: list[Flag]
    decision: Decision | None
    payment: PaymentResult | None
    corrections: list[Correction]
    extraction_method: str
    tool_calls: list[ToolCallRecord]


def _ingest(state: PipelineState, *, deps: Deps) -> dict[str, Any]:
    document = Document(
        path=Path(state["document_path"]),
        format=DocumentFormat(state["document_format"]),
        raw_text=state["raw_text"],
    )

    if document.format in _STRUCTURED_FORMATS:
        try:
            invoice = parse_structured(document)
            return {
                "invoice": invoice.model_dump(mode="json"),
                "extraction_method": "deterministic",
            }
        except StructuredParseFailed:
            pass  # Falls through to model extraction below.

    result = extract_invoice(document, deps)
    return {
        "invoice": result.invoice.model_dump(mode="json"),
        "extraction_method": "model",
        "corrections": [c.model_dump(mode="json") for c in result.corrections],
        # Extraction-stage flags (e.g. an unresolvable due date) start the flags list;
        # validate appends to it rather than replacing it, so neither stage's findings
        # overwrite the other's.
        "flags": [f.model_dump(mode="json") for f in result.flags],
    }


def _validate(state: PipelineState, *, deps: Deps) -> dict[str, Any]:
    invoice = Invoice.model_validate(state["invoice"])
    existing_flags = [Flag.model_validate(f) for f in state.get("flags", [])]
    existing_corrections = [Correction.model_validate(c) for c in state.get("corrections", [])]

    result = validate_invoice(invoice, deps)

    return {
        "flags": [f.model_dump(mode="json") for f in existing_flags + result.flags],
        "corrections": [
            c.model_dump(mode="json") for c in existing_corrections + result.corrections
        ],
        "usd_total": str(result.usd_total) if result.usd_total is not None else None,
    }


def _approve(state: PipelineState, *, deps: Deps) -> dict[str, Any]:
    invoice = Invoice.model_validate(state["invoice"])
    flags = [Flag.model_validate(f) for f in state.get("flags", [])]
    usd_total_str = state.get("usd_total")
    usd_total = Decimal(usd_total_str) if usd_total_str is not None else None

    rule_decision = decide(invoice, flags, usd_total=usd_total)

    if rule_decision.outcome == "rejected":
        # The ratchet forbids downgrading a rejection, so the agent call cannot change
        # anything here — it is not made at all, not made and then ignored.
        return {"decision": rule_decision.model_dump(mode="json"), "tool_calls": []}

    decision, tool_calls = run_approval_agent(invoice, flags, usd_total, rule_decision, deps)
    return {
        "decision": decision.model_dump(mode="json"),
        "tool_calls": [tc.model_dump(mode="json") for tc in tool_calls],
    }


def _pay(state: PipelineState, *, deps: Deps) -> dict[str, Any]:
    decision = Decision.model_validate(state["decision"])
    if decision.outcome != "approved":
        # Explicit None, not an empty dict. The checkpointer persists state by
        # thread_id (the document name), so a later run reprocessing the same
        # document reuses that checkpoint. Returning {} leaves a prior run's
        # "payment" key untouched — a stale success record would silently survive
        # onto an invoice that was just rejected or escalated. Emitting the key
        # ourselves is what makes this run's outcome replace the last one's.
        return {"payment": None}

    invoice = Invoice.model_validate(state["invoice"])
    total = invoice.total if invoice.total is not None else invoice.line_items_total
    assert total is not None  # approval could not have approved without one

    identity = normalize_invoice_identity(invoice.invoice_number)
    if identity is not None and deps.registry.payment_recorded(identity):
        # Idempotency: a prior run already paid this identity. Reprocessing the same
        # document (or a batch run over the same directory a second time) must not pay
        # it again — the mock payment function is never called here.
        return {
            "payment": {
                "status": "skipped",
                "reference": None,
                "detail": f"already paid under identity {identity!r}",
            }
        }

    result = deps.payment.pay(invoice.vendor.name, total, invoice.currency)
    if identity is not None:
        try:
            deps.registry.record_payment(identity, invoice.vendor.name, total)
        except DuplicatePayment:
            # The check above and this write are not atomic, but nothing in this
            # sequential, single-threaded pipeline can run between them (ADR-0002 —
            # concurrency is a documented gap, not solved here). This branch is a
            # backstop the storage-layer UNIQUE constraint provides for free, not a
            # race this code is actually exposed to.
            pass
    return {
        "payment": {
            "status": result.status,
            "reference": result.reference,
            "detail": result.detail,
        }
    }


def build_graph(deps: Deps, checkpointer: BaseCheckpointSaver[Any]) -> Any:
    """Wire the four stages. Deps are bound into the nodes at construction, so a node's
    signature stays `(state) -> dict` — what LangGraph expects — without a global."""
    builder = StateGraph(PipelineState)
    builder.add_node("ingest", lambda state: _ingest(state, deps=deps))
    builder.add_node("validate", lambda state: _validate(state, deps=deps))
    builder.add_node("approve", lambda state: _approve(state, deps=deps))
    builder.add_node("pay", lambda state: _pay(state, deps=deps))

    builder.add_edge(START, "ingest")
    builder.add_edge("ingest", "validate")
    builder.add_edge("validate", "approve")
    builder.add_edge("approve", "pay")
    builder.add_edge("pay", END)

    return builder.compile(checkpointer=checkpointer)


@contextmanager
def _checkpointer(
    given: BaseCheckpointSaver[Any] | None,
) -> Iterator[BaseCheckpointSaver[Any]]:
    """Use the caller's checkpointer, or an ephemeral in-memory one, closed afterward.

    Structured as a context manager rather than an if-branch so the connection this
    function opens is also the connection it closes — the same discipline as every other
    SQLite use in this codebase (see the review fix in ticket 01).
    """
    if given is not None:
        yield given
        return

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    try:
        yield SqliteSaver(conn)
    finally:
        conn.close()


def run_invoice(
    document: Document,
    deps: Deps,
    *,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> RunResult:
    """The primary seam. A document goes in; the whole outcome comes out.

    Without an explicit checkpointer, an ephemeral in-memory one is used — the graph
    still checkpoints after every node, so the behaviour under test is real, but nothing
    touches disk. The CLI passes a persistent one so a run's trace survives the process.
    """
    with _checkpointer(checkpointer) as active:
        graph = build_graph(deps, active)
        config: RunnableConfig = {"configurable": {"thread_id": document.name}}

        initial: PipelineState = {
            "document_path": str(document.path),
            "document_format": document.format.value,
            "document_name": document.name,
            "raw_text": document.raw_text,
            "flags": [],
            "corrections": [],
        }
        final = graph.invoke(initial, config=config)

    return RunResult(
        invoice=Invoice.model_validate(final["invoice"]) if final.get("invoice") else None,
        flags=[Flag.model_validate(f) for f in final.get("flags", [])],
        decision=Decision.model_validate(final["decision"]) if final.get("decision") else None,
        payment=PaymentResult(**final["payment"]) if final.get("payment") else None,
        corrections=[Correction.model_validate(c) for c in final.get("corrections", [])],
        extraction_method=final.get("extraction_method", "model"),
        tool_calls=[ToolCallRecord.model_validate(tc) for tc in final.get("tool_calls", [])],
    )
