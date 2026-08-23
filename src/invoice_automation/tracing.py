"""Per-run trace: every stage the graph executed and every LLM call it made, persisted
so a run can be inspected after the fact rather than re-run to find out what happened.

A "run" is one invoice's journey through the graph (CONTEXT.md), identified by the same
run id as its structured log lines — the document name, which is already the graph's
own `thread_id` (graph.py).
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator, Protocol, runtime_checkable

from langgraph.errors import GraphInterrupt

from .structured_logging import log_event


@dataclass(frozen=True)
class StageEvent:
    """One stage the graph executed, and how long it took."""

    run_id: str
    stage: str
    duration_ms: float
    ok: bool
    detail: str | None = None


@dataclass(frozen=True)
class LlmCallEvent:
    """One LLM call — real or served from cache — and its cost."""

    run_id: str
    kind: str
    """"extract", "critique", or "converse" — StructuredCall.kind, or "converse" for a
    tool-calling turn."""
    cache_hit: bool
    latency_ms: float
    prompt_tokens: int
    completion_tokens: int
    cost_usd: Decimal
    prompt: str
    """The request verbatim — system+user for a structured call, the message list for a
    conversational one — so an engineer inspecting a run's trace can see what was
    actually asked, not just how big it was."""
    response: str
    """The response verbatim, JSON-shaped. Recorded here rather than in the structured
    log line, which stays terse — the full text belongs in the trace, queryable per
    run, not repeated into every log line a batch produces."""


@runtime_checkable
class Tracer(Protocol):
    def stage_started(self, run_id: str, stage: str) -> None:
        """A stage began executing — not part of the persisted trace (ticket 12 only
        records completed stages), just a live signal ticket 13's SSE stream reads so it
        can report a stage's entry as well as its exit."""
        ...

    def record_stage(self, event: StageEvent) -> None: ...

    def record_llm_call(self, event: LlmCallEvent) -> None: ...

    def stages_for(self, run_id: str) -> list[StageEvent]: ...

    def llm_calls_for(self, run_id: str) -> list[LlmCallEvent]: ...

    def cost_for(self, run_id: str) -> Decimal:
        """Total cost across every LLM call recorded for `run_id`."""
        ...

    def run_ids(self) -> list[str]:
        """Every run id that has executed at least one stage — the population an
        escalation-rate report (overrides.py) divides by."""
        ...


@dataclass
class InMemoryTracer:
    """Scoped to one process. What tests and the default `Deps` use."""

    stages: list[StageEvent] = field(default_factory=list)
    llm_calls: list[LlmCallEvent] = field(default_factory=list)

    def stage_started(self, run_id: str, stage: str) -> None:
        pass

    def record_stage(self, event: StageEvent) -> None:
        self.stages.append(event)

    def record_llm_call(self, event: LlmCallEvent) -> None:
        self.llm_calls.append(event)

    def stages_for(self, run_id: str) -> list[StageEvent]:
        return [e for e in self.stages if e.run_id == run_id]

    def llm_calls_for(self, run_id: str) -> list[LlmCallEvent]:
        return [e for e in self.llm_calls if e.run_id == run_id]

    def cost_for(self, run_id: str) -> Decimal:
        return sum((e.cost_usd for e in self.llm_calls_for(run_id)), Decimal("0"))

    def run_ids(self) -> list[str]:
        seen: dict[str, None] = {}
        for event in self.stages:
            seen.setdefault(event.run_id, None)
        return list(seen)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS stage_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL,
    stage       TEXT NOT NULL,
    duration_ms REAL NOT NULL,
    ok          INTEGER NOT NULL,
    detail      TEXT
);
CREATE TABLE IF NOT EXISTS llm_call_events (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id            TEXT NOT NULL,
    kind              TEXT NOT NULL,
    cache_hit         INTEGER NOT NULL,
    latency_ms        REAL NOT NULL,
    prompt_tokens     INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    cost_usd          TEXT NOT NULL,
    prompt            TEXT NOT NULL,
    response          TEXT NOT NULL
);
"""


class SqliteTracer:
    """Persists across processes, so a run's trace survives after the CLI exits — what
    the checklist calls "queryable after the run"."""

    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.executescript(_SCHEMA)

    def stage_started(self, run_id: str, stage: str) -> None:
        pass

    def record_stage(self, event: StageEvent) -> None:
        with closing(sqlite3.connect(self._path)) as conn, conn:
            conn.execute(
                "INSERT INTO stage_events (run_id, stage, duration_ms, ok, detail) "
                "VALUES (?, ?, ?, ?, ?)",
                (event.run_id, event.stage, event.duration_ms, int(event.ok), event.detail),
            )

    def record_llm_call(self, event: LlmCallEvent) -> None:
        with closing(sqlite3.connect(self._path)) as conn, conn:
            conn.execute(
                "INSERT INTO llm_call_events (run_id, kind, cache_hit, latency_ms, "
                "prompt_tokens, completion_tokens, cost_usd, prompt, response) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.run_id,
                    event.kind,
                    int(event.cache_hit),
                    event.latency_ms,
                    event.prompt_tokens,
                    event.completion_tokens,
                    str(event.cost_usd),
                    event.prompt,
                    event.response,
                ),
            )

    def stages_for(self, run_id: str) -> list[StageEvent]:
        with closing(sqlite3.connect(self._path)) as conn:
            rows = conn.execute(
                "SELECT run_id, stage, duration_ms, ok, detail FROM stage_events "
                "WHERE run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
        return [StageEvent(r[0], r[1], r[2], bool(r[3]), r[4]) for r in rows]

    def llm_calls_for(self, run_id: str) -> list[LlmCallEvent]:
        with closing(sqlite3.connect(self._path)) as conn:
            rows = conn.execute(
                "SELECT run_id, kind, cache_hit, latency_ms, prompt_tokens, "
                "completion_tokens, cost_usd, prompt, response FROM llm_call_events "
                "WHERE run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
        return [
            LlmCallEvent(r[0], r[1], bool(r[2]), r[3], r[4], r[5], Decimal(r[6]), r[7], r[8])
            for r in rows
        ]

    def cost_for(self, run_id: str) -> Decimal:
        return sum((e.cost_usd for e in self.llm_calls_for(run_id)), Decimal("0"))

    def run_ids(self) -> list[str]:
        with closing(sqlite3.connect(self._path)) as conn:
            rows = conn.execute(
                "SELECT run_id FROM stage_events GROUP BY run_id ORDER BY MIN(id)"
            ).fetchall()
        return [r[0] for r in rows]


@contextmanager
def traced_stage(tracer: Tracer, run_id: str, stage: str) -> Iterator[None]:
    """Time `stage` for `run_id` and record it — unless the graph is only pausing here
    for a human review (ADR-0005). `interrupt()` raises `GraphInterrupt` to pause the
    graph, and the node re-runs from the top on resume; recording a "failed" stage for
    that would be wrong twice over — it isn't a failure, and the eventual successful
    completion already gets its own entry.
    """
    tracer.stage_started(run_id, stage)
    start = time.perf_counter()
    try:
        yield
    except GraphInterrupt:
        raise
    except Exception as exc:
        duration_ms = (time.perf_counter() - start) * 1000
        tracer.record_stage(StageEvent(run_id, stage, duration_ms, False, str(exc)))
        log_event(run_id, "stage_failed", stage=stage, duration_ms=duration_ms, error=str(exc))
        raise
    else:
        duration_ms = (time.perf_counter() - start) * 1000
        tracer.record_stage(StageEvent(run_id, stage, duration_ms, True, None))
        log_event(run_id, "stage_complete", stage=stage, duration_ms=duration_ms)
