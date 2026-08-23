"""The reasoning engine, behind one narrow interface.

Per ADR-0001 this is the only real network dependency in the system; every Acme-side
system is local. The interface is deliberately small — one structured call — so that Grok,
a recorded cassette, or a hand-written fake are interchangeable.

This ticket ships the fake. The Grok implementation arrives with ticket 03.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

_INVOICE_NUMBER = re.compile(r"\bINV[- ]?(\d{4})\b|\bInv\s*#:\s*(\d{4})\b", re.IGNORECASE)

SAMPLE_RESPONSES_DIR = Path(__file__).parent / "sample_responses"


@dataclass(frozen=True)
class StructuredCall:
    """What was asked of the provider. Recorded in the trace; replayed by the fake."""

    system: str
    user: str
    schema: dict[str, Any]


@runtime_checkable
class Provider(Protocol):
    def structured(self, call: StructuredCall) -> dict[str, Any]:
        """Return a JSON object conforming to `call.schema`.

        Implementations constrain the model to the schema rather than asking politely
        for JSON. Validation happens above this layer, so a provider may return
        something invalid and the caller decides what to do about it.
        """
        ...


@dataclass
class FakeProvider:
    """Replays canned responses. No network, no key, fully deterministic.

    Keyed on the invoice number found in the prompt, because that is the one stable
    identifier a document carries. A prompt whose invoice is unknown raises rather than
    returning something plausible — a fake that silently invents data is worse than no
    fake at all.

    Ticket 18 replaces the canned responses with real recorded ones. The lookup
    mechanism does not change.
    """

    responses: dict[str, dict[str, Any]] = field(default_factory=dict)
    calls: list[StructuredCall] = field(default_factory=list)

    @classmethod
    def with_sample_responses(cls) -> FakeProvider:
        """Load the responses shipped alongside the package."""
        responses: dict[str, dict[str, Any]] = {}
        if SAMPLE_RESPONSES_DIR.is_dir():
            for path in sorted(SAMPLE_RESPONSES_DIR.glob("*.json")):
                responses[path.stem.upper()] = json.loads(path.read_text(encoding="utf-8"))
        return cls(responses=responses)

    def structured(self, call: StructuredCall) -> dict[str, Any]:
        self.calls.append(call)
        key = _response_key(call.user)
        if key is None:
            raise LookupError(
                "FakeProvider could not find an invoice number in the prompt, so it has "
                "no way to choose a response. Known responses: "
                f"{sorted(self.responses)}"
            )
        if key not in self.responses:
            raise LookupError(
                f"FakeProvider has no recorded response for {key}. "
                f"Known responses: {sorted(self.responses)}. "
                "Record one, or run against a real provider."
            )
        return self.responses[key]


def _response_key(prompt: str) -> str | None:
    """The invoice number in a prompt, normalised to `INV-nnnn`."""
    match = _INVOICE_NUMBER.search(prompt)
    if match is None:
        return None
    digits = match.group(1) or match.group(2)
    return f"INV-{digits}"
