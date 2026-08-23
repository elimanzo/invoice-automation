"""Structured logging: one JSON object per line, always carrying a run id.

A run id (CONTEXT.md: "one invoice's journey through the graph") appears in every log
line for it, so a single invoice's activity can be isolated from a batch by filtering on
that field alone, without parsing prose.
"""

from __future__ import annotations

import json
import logging
from typing import Any

LOGGER_NAME = "invoice_automation"


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
        }
        payload.update(getattr(record, "fields", {}))
        return json.dumps(payload)


def configure_logging(level: int = logging.INFO) -> None:
    """Wire the package logger to emit one JSON line per record to stderr.

    Idempotent — clears any handlers from a prior call first, so calling it more than
    once (the CLI, and any test that exercises it) never doubles output.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False


def log_event(run_id: str, event: str, **fields: Any) -> None:
    """Emit one structured log line for `run_id`.

    `event` names what happened ("stage_complete", "llm_call", ...); everything else is
    caller-supplied context. Every line carries `run_id` and `event` regardless of what
    the caller passes, so those two fields are never accidentally omitted.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.info(event, extra={"fields": {"run_id": run_id, "event": event, **fields}})
