"""Human overrides: labelled data for how well escalation is calibrated.

Every escalated invoice reaches a human by definition (ADR-0005) — the system's own
decision at that point is always "escalated", never a side. What the human then decides
is the only place a system-vs-human comparison exists at all, so each one is recorded as
its own outcome: what the system decided (and why), what the human decided, and the
reason given. `build_override_report` turns the accumulated overrides into the two
numbers a controller actually wants — how often escalation fires, and how often it fires
for nothing.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict


class Override(BaseModel):
    """One escalation resolved by a human."""

    model_config = ConfigDict(frozen=True)

    document_name: str
    system_outcome: str
    system_reasoning: str
    human_outcome: str
    reason: str


@runtime_checkable
class OverrideStore(Protocol):
    def record(self, override: Override) -> None: ...

    def list(self) -> list[Override]:
        """Every override recorded, oldest first."""
        ...


@dataclass
class InMemoryOverrideStore:
    """Scoped to one process. What tests and the default `Deps` use."""

    overrides: list[Override] = field(default_factory=list)

    def record(self, override: Override) -> None:
        self.overrides.append(override)

    def list(self) -> list[Override]:
        return list(self.overrides)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS overrides (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    document_name    TEXT NOT NULL,
    system_outcome   TEXT NOT NULL,
    system_reasoning TEXT NOT NULL,
    human_outcome    TEXT NOT NULL,
    reason           TEXT NOT NULL
);
"""


class SqliteOverrideStore:
    """Persists across processes — overrides accumulate across every run the system
    has ever escalated, not just the current process's."""

    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.executescript(_SCHEMA)

    def record(self, override: Override) -> None:
        with closing(sqlite3.connect(self._path)) as conn, conn:
            conn.execute(
                "INSERT INTO overrides (document_name, system_outcome, system_reasoning, "
                "human_outcome, reason) VALUES (?, ?, ?, ?, ?)",
                (
                    override.document_name,
                    override.system_outcome,
                    override.system_reasoning,
                    override.human_outcome,
                    override.reason,
                ),
            )

    def list(self) -> list[Override]:
        with closing(sqlite3.connect(self._path)) as conn:
            rows = conn.execute(
                "SELECT document_name, system_outcome, system_reasoning, human_outcome, "
                "reason FROM overrides ORDER BY id"
            ).fetchall()
        return [
            Override(
                document_name=r[0],
                system_outcome=r[1],
                system_reasoning=r[2],
                human_outcome=r[3],
                reason=r[4],
            )
            for r in rows
        ]


@dataclass(frozen=True)
class OverrideReport:
    """What a controller wants to know about the escalation threshold: how often it
    fires, and whether it's firing on things that turn out fine.

    `disagreements` lists every escalation a human then approved — the system asked for
    scrutiny and the answer was "no problem here", which is exactly what a threshold set
    too tight looks like. An escalation a human then rejected is not a disagreement: the
    system's caution was warranted.
    """

    total_runs: int
    total_escalations: int
    approved_count: int
    rejected_count: int
    disagreements: tuple[Override, ...]

    @property
    def escalation_rate(self) -> float:
        if self.total_runs == 0:
            return 0.0
        return self.total_escalations / self.total_runs

    @property
    def approved_share(self) -> float:
        """The share of escalations a human then approved — CONTEXT.md's own example
        of what makes a threshold too tight visible rather than assumed."""
        if self.total_escalations == 0:
            return 0.0
        return self.approved_count / self.total_escalations


def build_override_report(overrides: list[Override], *, total_runs: int) -> OverrideReport:
    approved = [o for o in overrides if o.human_outcome == "approved"]
    rejected = [o for o in overrides if o.human_outcome == "rejected"]
    return OverrideReport(
        total_runs=total_runs,
        total_escalations=len(overrides),
        approved_count=len(approved),
        rejected_count=len(rejected),
        disagreements=tuple(approved),
    )


def render_override_report(report: OverrideReport) -> str:
    """Human-readable rendering, the same style as `cli.render_batch`."""
    lines = [
        f"Runs:               {report.total_runs}",
        f"Escalations:        {report.total_escalations} "
        f"({report.escalation_rate:.1%} of runs)",
        f"Human-approved:     {report.approved_count} "
        f"({report.approved_share:.1%} of escalations)",
        f"Human-rejected:     {report.rejected_count}",
        "",
        "Escalations a human then approved (the escalation added no value here):",
    ]
    lines += [
        f"  {o.document_name}: {o.system_reasoning} -> approved ({o.reason})"
        for o in report.disagreements
    ] or ["  (none)"]
    return "\n".join(lines)
