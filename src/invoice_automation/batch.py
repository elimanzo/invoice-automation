"""Batch mode: every document in a directory, one after another.

Sequential by design (ADR-0002) — concurrency is a config value for a later ticket to
read, not a rewrite this one needs to do. A directory of real invoices always contains
at least one that fails somehow (that is the point of the sample data), so per-document
isolation is not a nice-to-have here: a batch that aborts on its first bad document would
never finish at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver

from .deps import Deps
from .documents import UnsupportedDocument, load_document
from .graph import RunResult, run_invoice

# A hook for a later ticket, not a mechanism this one implements — sequential is what
# ADR-0002 specifies, and the registry race concurrency would introduce is a documented
# gap (ADR-0002), not something this constant on its own solves.
BATCH_CONCURRENCY = 1


@dataclass(frozen=True)
class BatchItem:
    """One document's outcome, or why it didn't get one."""

    document_name: str
    result: RunResult | None
    error: str | None

    @property
    def outcome(self) -> str:
        """"failed" when an exception stopped this document short of a decision,
        otherwise the decision's own outcome — the label a run summary counts by."""
        if self.result is None or self.result.decision is None:
            return "failed"
        return self.result.decision.outcome


@dataclass(frozen=True)
class BatchSummary:
    """Every item processed, and the counts a clerk actually wants to see."""

    items: list[BatchItem]

    @property
    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.items:
            counts[item.outcome] = counts.get(item.outcome, 0) + 1
        return counts


def run_batch(
    directory: Path,
    deps: Deps,
    *,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> BatchSummary:
    """Process every document in `directory`, sequentially.

    A file this system doesn't read at all (`UnsupportedDocument` — wrong extension,
    unrecognisable content) is skipped without becoming a batch item; anything else that
    goes wrong for one document — a bad response, a validation error, whatever — is
    caught broadly on purpose. This is the one place in the codebase where catching
    `Exception` is the point rather than a smell: a batch's whole job is to survive any
    single document's failure and keep going, and the brief never anticipated which
    failure mode would hit which document.
    """
    items: list[BatchItem] = []

    for path in sorted(p for p in directory.iterdir() if p.is_file()):
        try:
            document = load_document(path)
        except UnsupportedDocument:
            continue
        except Exception as exc:  # noqa: BLE001 — see the module and function docstrings
            items.append(BatchItem(document_name=path.name, result=None, error=str(exc)))
            continue

        try:
            result = run_invoice(document, deps, checkpointer=checkpointer)
        except Exception as exc:  # noqa: BLE001 — see the module and function docstrings
            items.append(BatchItem(document_name=path.name, result=None, error=str(exc)))
            continue

        items.append(BatchItem(document_name=path.name, result=result, error=None))

    return BatchSummary(items=items)
