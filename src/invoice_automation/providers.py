"""The reasoning engine, behind one narrow interface.

Per ADR-0001 this is the only real network dependency in the system; every Acme-side
system is local. The interface is deliberately small — one structured call — so that Grok,
a recorded cassette, or a hand-written fake are interchangeable.

This ticket ships the fake. The Grok implementation arrives with ticket 03.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

SAMPLE_RESPONSES_DIR = Path(__file__).parent / "sample_responses"


@dataclass(frozen=True)
class StructuredCall:
    """What was asked of the provider. Recorded in the trace; replayed by the fake."""

    system: str
    user: str
    schema: dict[str, Any]
    document_id: str
    """Which document this call is about.

    Carried explicitly rather than parsed back out of the prompt. Two documents in the
    sample data state the same invoice number — an invoice and its revision — so invoice
    number does not identify a call, and recovering an identifier by regex over prompt
    text would silently return the wrong recording.
    """


@runtime_checkable
class Provider(Protocol):
    def structured(self, call: StructuredCall) -> dict[str, Any]:
        """Return a JSON object conforming to `call.schema`.

        Implementations constrain the model to the schema rather than asking politely
        for JSON. Validation happens above this layer, so a provider may return
        something invalid and the caller decides what to do about it.
        """
        ...


class MissingRecording(LookupError):
    """No recorded response exists for this document."""


@dataclass
class FakeProvider:
    """Replays recorded responses. No network, no key, fully deterministic.

    Keyed on document identity, so an invoice and its revision never collide. A document
    with no recording raises rather than returning something plausible — a fake that
    silently invents data is worse than no fake at all, and the same reasoning applies to
    an empty response set: that raises at construction rather than failing later with a
    confusing per-document error.

    Ticket 18 replaces these responses with real recorded ones. The lookup does not change.
    """

    responses: dict[str, dict[str, Any]] = field(default_factory=dict)
    calls: list[StructuredCall] = field(default_factory=list)

    @classmethod
    def with_sample_responses(cls, directory: Path | None = None) -> FakeProvider:
        """Load the responses shipped alongside the package."""
        directory = directory or SAMPLE_RESPONSES_DIR
        if not directory.is_dir():
            raise FileNotFoundError(
                f"No recorded responses at {directory}. The package ships them as package "
                "data; a build that omits them leaves the fake provider unable to answer "
                "anything."
            )
        responses = {
            path.stem: json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(directory.glob("*.json"))
        }
        if not responses:
            raise FileNotFoundError(f"No recorded responses found in {directory}.")
        return cls(responses=responses)

    def structured(self, call: StructuredCall) -> dict[str, Any]:
        self.calls.append(call)
        key = Path(call.document_id).stem
        if key not in self.responses:
            raise MissingRecording(
                f"No recorded response for {call.document_id!r}. "
                f"Recorded: {sorted(self.responses)}. "
                "Record one, or run against a real provider."
            )
        # A copy, so a caller mutating the result cannot corrupt the store for later calls.
        return copy.deepcopy(self.responses[key])
