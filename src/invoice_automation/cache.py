"""Content-hash cache for LLM requests.

Keyed on the full request — system prompt, user prompt, and schema for a structured
call; the message/tool state for a conversational one — so a repeated development run
over the same documents costs nothing after the first pass, and the key changes the
instant anything about the request does (a prompt tweak, a schema change, a new turn in
a conversation).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


def hash_request(payload: dict[str, Any]) -> str:
    """A stable content hash for `payload`. `sort_keys` makes it independent of dict
    insertion order; `default=str` copes with anything not natively JSON-shaped
    (Decimal, etc.) landing in the payload, the same tolerance json.dumps needs
    elsewhere in this codebase for domain values."""
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@runtime_checkable
class LLMCache(Protocol):
    def get(self, key: str) -> dict[str, Any] | None:
        """The cached response for `key`, or None on a miss."""
        ...

    def set(self, key: str, value: dict[str, Any]) -> None:
        """Record the response for `key`, replacing whatever was there before."""
        ...


class NullCache:
    """Always misses. What `--provider` verification runs use (config: cache_enabled
    = False) — every call must be a real one, never served from a prior run's answer."""

    def get(self, key: str) -> dict[str, Any] | None:
        return None

    def set(self, key: str, value: dict[str, Any]) -> None:
        pass


@dataclass
class InMemoryCache:
    """Scoped to one process. What tests and the default `Deps` use — no disk, no
    cross-process durability, but enough to prove a repeated call within one session
    is served from cache rather than issuing a second real call."""

    store: dict[str, dict[str, Any]] = field(default_factory=dict)

    def get(self, key: str) -> dict[str, Any] | None:
        return self.store.get(key)

    def set(self, key: str, value: dict[str, Any]) -> None:
        self.store[key] = value


_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_cache (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class SqliteCache:
    """Persists across processes, so a development run over the same documents costs
    nothing after the first pass even after the process restarts — the case an
    in-memory cache cannot cover."""

    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.executescript(_SCHEMA)

    def get(self, key: str) -> dict[str, Any] | None:
        with closing(sqlite3.connect(self._path)) as conn:
            row = conn.execute("SELECT value FROM llm_cache WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row is not None else None

    def set(self, key: str, value: dict[str, Any]) -> None:
        with closing(sqlite3.connect(self._path)) as conn, conn:
            conn.execute(
                "INSERT INTO llm_cache (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)),
            )
