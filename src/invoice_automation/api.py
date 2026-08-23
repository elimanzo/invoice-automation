"""HTTP API over the pipeline (ticket 13).

Thin by design: every endpoint delegates to `run_invoice`, `resume_invoice`, or
`run_batch` — the same primary seam and resume entry point the CLI uses — plus
`graph.get_state` for reading a run's checkpointed state back out. Nothing here
reimplements ingestion, validation, approval, or payment.

A "run" is identified by the document name (same as the graph's `thread_id`). Listing
runs walks `deps.tracer.run_ids()` — the population every run has registered at least
one stage against — and reads each one's full state from the checkpointer.
"""

from __future__ import annotations

import json
import queue
import threading
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from pydantic import BaseModel

from .batch import BatchSummary, run_batch
from .config import MANUAL_COST_PER_INVOICE_USD, MANUAL_PROCESSING_DAYS
from .deps import Deps
from .documents import (
    UndecodableDocument,
    UnreadableDocument,
    UnsupportedDocument,
    load_document,
)
from .graph import NotAwaitingReview, build_graph
from .graph import resume_invoice as _resume_invoice
from .graph import run_invoice as _run_invoice
from .models import (
    Correction,
    Decision,
    Flag,
    FlagSeverity,
    HumanReview,
    Invoice,
    ToolCallRecord,
)
from .tracing import LlmCallEvent, StageEvent, Tracer


class RunEventBus:
    """Fans stage transitions out to every connected SSE subscriber.

    Plain thread-safe `queue.Queue`s, not asyncio-native: a pipeline run executes on an
    ordinary thread (a `BackgroundTasks` worker), so publishing has to work from there.
    Each subscriber's async generator bridges its queue back onto the event loop with
    `asyncio.to_thread` (see `_events` below).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[queue.Queue[dict[str, Any]]] = []

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        q: queue.Queue[dict[str, Any]] = queue.Queue()
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            q.put(event)


class _BroadcastingTracer:
    """Wraps a `Tracer`, forwarding every call unchanged and additionally publishing
    stage entry/exit onto an event bus — the source ticket 13's SSE stream reads from,
    without any pipeline stage knowing an API is watching."""

    def __init__(self, inner: Tracer, bus: RunEventBus) -> None:
        self._inner = inner
        self._bus = bus

    def stage_started(self, run_id: str, stage: str) -> None:
        self._inner.stage_started(run_id, stage)
        self._bus.publish({"run_id": run_id, "stage": stage, "transition": "enter"})

    def record_stage(self, event: StageEvent) -> None:
        self._inner.record_stage(event)
        self._bus.publish(
            {
                "run_id": event.run_id,
                "stage": event.stage,
                "transition": "leave",
                "ok": event.ok,
            }
        )

    def record_llm_call(self, event: Any) -> None:
        self._inner.record_llm_call(event)

    def stages_for(self, run_id: str) -> list[StageEvent]:
        return self._inner.stages_for(run_id)

    def llm_calls_for(self, run_id: str) -> list[Any]:
        return self._inner.llm_calls_for(run_id)

    def cost_for(self, run_id: str) -> Any:
        return self._inner.cost_for(run_id)

    def run_ids(self) -> list[str]:
        return self._inner.run_ids()


class RunNotFound(Exception):
    """No checkpointed state exists under this document name."""


class RunSummary(BaseModel):
    document_name: str
    outcome: str | None
    amount: str | None
    currency: str | None
    flag_count: int
    timestamp: str | None
    vendor: str | None
    correction_count: int
    max_flag_severity: str | None


class LlmCallSummary(BaseModel):
    kind: str
    cache_hit: bool
    latency_ms: float
    prompt_tokens: int
    completion_tokens: int
    cost_usd: str
    prompt: str
    response: str


class RunDetail(BaseModel):
    document_name: str
    document_format: str | None
    raw_text: str | None
    invoice: Invoice | None
    flags: list[Flag]
    decision: Decision | None
    corrections: list[Correction]
    tool_calls: list[ToolCallRecord]
    human_review: HumanReview | None
    stages: list[dict[str, Any]]
    llm_calls: list[LlmCallSummary]
    awaiting_review: bool


class ImpactSummary(BaseModel):
    """The controller-facing business-impact strip (ticket 15): the system stated in
    the terms the business uses, not in pipeline vocabulary."""

    invoices_processed: int
    avg_processing_ms: float
    manual_baseline_days: float
    errors_caught: int
    dollars_flagged: str
    cost_per_invoice_usd: str
    manual_cost_per_invoice_usd: str


# Ranks a flag's control-flow weight (models.py's FlagSeverity) so the ledger can
# report the single worst severity per run without the frontend re-deriving the order.
_SEVERITY_RANK: dict[FlagSeverity, int] = {
    FlagSeverity.FATAL: 3,
    FlagSeverity.SOFT: 2,
    FlagSeverity.INFO: 1,
}


def _max_severity(flags: list[Flag]) -> str | None:
    if not flags:
        return None
    return max(flags, key=lambda f: _SEVERITY_RANK[f.severity]).severity.value


class ReviewDecisionRequest(BaseModel):
    outcome: str
    reason: str


class TriggerRequest(BaseModel):
    document_path: str | None = None
    directory_path: str | None = None


class TriggerResponse(BaseModel):
    status: str
    document_name: str | None = None


def _config_for(document_name: str) -> RunnableConfig:
    return {"configurable": {"thread_id": document_name}}


def _run_state(
    document_name: str, deps: Deps, checkpointer: BaseCheckpointSaver[Any]
) -> Any:
    graph = build_graph(deps, checkpointer)
    snapshot = graph.get_state(_config_for(document_name))
    if not snapshot.values:
        raise RunNotFound(document_name)
    return snapshot


def _to_summary(document_name: str, snapshot: Any) -> RunSummary:
    values = snapshot.values
    invoice = values.get("invoice")
    decision = values.get("decision")
    flags = [Flag.model_validate(f) for f in values.get("flags") or []]
    amount = None
    currency = None
    vendor = None
    if invoice is not None:
        amount = invoice.get("total")
        currency = invoice.get("currency")
        vendor = (invoice.get("vendor") or {}).get("name")
    return RunSummary(
        document_name=document_name,
        outcome=decision["outcome"] if decision else None,
        amount=str(amount) if amount is not None else None,
        currency=currency,
        flag_count=len(flags),
        timestamp=snapshot.created_at,
        vendor=vendor,
        correction_count=len(values.get("corrections") or []),
        max_flag_severity=_max_severity(flags),
    )


def _to_llm_call(event: LlmCallEvent) -> LlmCallSummary:
    return LlmCallSummary(
        kind=event.kind,
        cache_hit=event.cache_hit,
        latency_ms=event.latency_ms,
        prompt_tokens=event.prompt_tokens,
        completion_tokens=event.completion_tokens,
        cost_usd=str(event.cost_usd),
        prompt=event.prompt,
        response=event.response,
    )


def _to_detail(document_name: str, snapshot: Any, deps: Deps) -> RunDetail:
    values = snapshot.values
    invoice = values.get("invoice")
    stages = [
        {
            "stage": s.stage,
            "duration_ms": s.duration_ms,
            "ok": s.ok,
            "detail": s.detail,
        }
        for s in deps.tracer.stages_for(document_name)
    ]
    return RunDetail(
        document_name=document_name,
        document_format=values.get("document_format"),
        raw_text=values.get("raw_text"),
        invoice=Invoice.model_validate(invoice) if invoice else None,
        flags=[Flag.model_validate(f) for f in values.get("flags") or []],
        decision=Decision.model_validate(values["decision"]) if values.get("decision") else None,
        corrections=[Correction.model_validate(c) for c in values.get("corrections") or []],
        tool_calls=[ToolCallRecord.model_validate(tc) for tc in values.get("tool_calls") or []],
        human_review=HumanReview.model_validate(values["human_review"])
        if values.get("human_review")
        else None,
        stages=stages,
        llm_calls=[_to_llm_call(e) for e in deps.tracer.llm_calls_for(document_name)],
        awaiting_review=bool(snapshot.interrupts),
    )


def create_app(deps: Deps, checkpointer: BaseCheckpointSaver[Any]) -> FastAPI:
    """Build the FastAPI app. `deps` and `checkpointer` are shared across every request
    the same way the CLI shares one `Deps` and one on-disk checkpointer across a whole
    invocation — a run triggered here and a run inspected here read and write the same
    state."""
    bus = RunEventBus()
    observed_deps = replace(deps, tracer=_BroadcastingTracer(deps.tracer, bus))

    app = FastAPI(title="invoice-automation")

    def _process_document(path: Path) -> None:
        document = load_document(path)
        _run_invoice(document, observed_deps, checkpointer=checkpointer)

    def _process_directory(path: Path) -> BatchSummary:
        return run_batch(path, observed_deps, checkpointer=checkpointer)

    def _iter_snapshots() -> Iterator[tuple[str, Any]]:
        """Every run id the tracer has recorded a stage against, paired with its
        checkpointed state — the shared population `/runs` and `/reviews` both filter
        and summarize from."""
        for run_id in observed_deps.tracer.run_ids():
            try:
                yield run_id, _run_state(run_id, observed_deps, checkpointer)
            except RunNotFound:
                continue

    @app.get("/runs")
    def list_runs() -> list[RunSummary]:
        return [_to_summary(run_id, snapshot) for run_id, snapshot in _iter_snapshots()]

    @app.get("/runs/{document_name}")
    def get_run(document_name: str) -> RunDetail:
        try:
            snapshot = _run_state(document_name, observed_deps, checkpointer)
        except RunNotFound:
            raise HTTPException(status_code=404, detail=f"no such run: {document_name!r}")
        return _to_detail(document_name, snapshot, observed_deps)

    @app.get("/reviews")
    def list_reviews() -> list[RunSummary]:
        return [
            _to_summary(run_id, snapshot)
            for run_id, snapshot in _iter_snapshots()
            if snapshot.interrupts
        ]

    @app.post("/reviews/{document_name}")
    def submit_review(document_name: str, body: ReviewDecisionRequest) -> RunDetail:
        if body.outcome not in ("approved", "rejected"):
            raise HTTPException(
                status_code=422, detail="outcome must be 'approved' or 'rejected'"
            )
        review = HumanReview(outcome=body.outcome, reason=body.reason)  # type: ignore[arg-type]
        try:
            _resume_invoice(document_name, review, observed_deps, checkpointer=checkpointer)
        except NotAwaitingReview as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        snapshot = _run_state(document_name, observed_deps, checkpointer)
        return _to_detail(document_name, snapshot, observed_deps)

    @app.post("/runs", status_code=202)
    def trigger_run(body: TriggerRequest, background_tasks: BackgroundTasks) -> TriggerResponse:
        if bool(body.document_path) == bool(body.directory_path):
            raise HTTPException(
                status_code=422,
                detail="pass exactly one of document_path or directory_path",
            )

        if body.document_path is not None:
            path = Path(body.document_path)
            try:
                document = load_document(path)
            except (
                FileNotFoundError,
                UnsupportedDocument,
                UndecodableDocument,
                UnreadableDocument,
            ) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            background_tasks.add_task(_run_invoice, document, observed_deps, checkpointer=checkpointer)
            return TriggerResponse(status="processing", document_name=document.name)

        directory = Path(body.directory_path)  # type: ignore[arg-type]
        if not directory.is_dir():
            raise HTTPException(status_code=400, detail=f"no such directory: {directory}")
        background_tasks.add_task(_process_directory, directory)
        return TriggerResponse(status="processing", document_name=None)

    @app.get("/impact")
    def get_impact() -> ImpactSummary:
        runs = list(_iter_snapshots())
        invoices_processed = len(runs)

        total_duration_ms = 0.0
        errors_caught = 0
        dollars_flagged = Decimal("0")
        total_cost = Decimal("0")
        for run_id, snapshot in runs:
            total_duration_ms += sum(
                s.duration_ms for s in observed_deps.tracer.stages_for(run_id)
            )
            total_cost += observed_deps.tracer.cost_for(run_id)

            flags = [Flag.model_validate(f) for f in snapshot.values.get("flags") or []]
            caught = [f for f in flags if f.severity != FlagSeverity.INFO]
            errors_caught += len(caught)
            if caught:
                invoice = snapshot.values.get("invoice")
                total = invoice.get("total") if invoice else None
                if total is not None:
                    dollars_flagged += Decimal(str(total))

        avg_processing_ms = total_duration_ms / invoices_processed if invoices_processed else 0.0
        cost_per_invoice = total_cost / invoices_processed if invoices_processed else Decimal("0")

        return ImpactSummary(
            invoices_processed=invoices_processed,
            avg_processing_ms=avg_processing_ms,
            manual_baseline_days=float(MANUAL_PROCESSING_DAYS),
            errors_caught=errors_caught,
            dollars_flagged=str(dollars_flagged),
            cost_per_invoice_usd=str(cost_per_invoice),
            manual_cost_per_invoice_usd=str(MANUAL_COST_PER_INVOICE_USD),
        )

    @app.get("/events")
    def stream_events() -> StreamingResponse:
        return StreamingResponse(_events(bus), media_type="text/event-stream")

    return app


def _events(bus: RunEventBus) -> Iterator[str]:
    """Format each bus event as one SSE `data:` line. A blocking generator — Starlette
    runs a sync generator response body in its own thread, so `queue.Queue.get` blocking
    here does not stall the event loop the way it would in an async endpoint."""
    q = bus.subscribe()
    try:
        while True:
            event = q.get()
            yield f"data: {json.dumps(event)}\n\n"
    finally:
        bus.unsubscribe(q)
