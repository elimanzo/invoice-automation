"""The reasoning engine, behind one narrow interface.

Per ADR-0001 this is the only real network dependency in the system; every Acme-side
system is local. The interface is deliberately small — one structured call — so that Grok,
a recorded cassette, or a hand-written fake are interchangeable.

Two implementations ship here: `GrokProvider`, using the `openai` SDK against xAI's
OpenAI-compatible endpoint, and `FakeProvider`, which replays recorded responses with no
network and no key. `build_deps` (deps.py) picks between them based on whether a key is
configured — nothing in the pipeline names either implementation directly.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from openai import OpenAI, OpenAIError

SAMPLE_RESPONSES_DIR = Path(__file__).parent / "sample_responses"

TOOL_NAME = "record_invoice"


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
    kind: str = "extract"
    """What this call is for: "extract" or "critique". Grok doesn't care — it just gets
    a schema and a prompt either way. FakeProvider uses it to know that an unrecorded
    critique call should default to "no problem found" rather than raise, so adding the
    critique step didn't require a second recorded response for every existing test."""


@runtime_checkable
class Provider(Protocol):
    def structured(self, call: StructuredCall) -> dict[str, Any]:
        """Return a JSON object conforming to `call.schema`.

        Implementations constrain the model to the schema rather than asking politely
        for JSON. Validation happens above this layer, so a provider may return
        something invalid and the caller decides what to do about it — extraction.py's
        retry loop is that caller.
        """
        ...


class MalformedProviderResponse(Exception):
    """The provider replied, but not with something `json.loads` can parse.

    Distinct from a schema violation (which `Invoice.model_validate` catches one layer
    up): this is the provider failing to hold up its side of the tool-call contract at
    all — no tool call, or a tool call whose arguments aren't valid JSON.
    """


class ProviderUnavailable(Exception):
    """The provider could not be reached, or refused the request outright.

    Auth failure, an empty API key, a network outage, a rate limit, a billing problem —
    anything the transport layer raises before a response worth judging ever exists.
    Distinct from `MalformedProviderResponse`, which means a response arrived and was
    junk: this means no usable response arrived at all, so extraction's retry loop does
    not catch it — retrying an expired key wastes calls without ever succeeding. It
    propagates to the CLI, which reports it the same clean way as every other failure.
    """


class GrokProvider:
    """Grok via xAI's OpenAI-compatible API.

    The invoice schema is sent as a function definition and the model is forced to call
    it (`tool_choice` names the function explicitly) rather than asked to emit JSON in
    prose — the mechanism ADR-0001 and this ticket call "schema-constrained output".
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        client: OpenAI | None = None,
    ) -> None:
        # `client` is injectable so tests can supply a stand-in with the same shape as
        # the OpenAI SDK's response objects, without a real key or a network call.
        # A live call once took over two minutes: the SDK's own default retry-with-
        # backoff compounding with a slow response. A bounded timeout plus a single
        # SDK-level retry means a stall surfaces as ProviderUnavailable in a predictable
        # window, rather than the whole CLI appearing to hang.
        self._client = client or OpenAI(
            api_key=api_key, base_url=base_url, timeout=30.0, max_retries=1
        )
        self._model = model

    def structured(self, call: StructuredCall) -> dict[str, Any]:
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": call.system},
                    {"role": "user", "content": call.user},
                ],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": TOOL_NAME,
                            "description": "Record the invoice extracted from the document.",
                            "parameters": call.schema,
                        },
                    }
                ],
                tool_choice={"type": "function", "function": {"name": TOOL_NAME}},
                # Extraction is reading comprehension, not creative writing: the same
                # document should produce the same answer every time. Found live —
                # invoice_1002.txt's "Dt:" (an abbreviated label) was read correctly on
                # one call and dropped on another, identical, call. Default sampling
                # temperature was the reason; there was never one before this fix.
                temperature=0,
            )
        except OpenAIError as exc:
            raise ProviderUnavailable(
                f"Grok request failed for {call.document_id!r}: {exc}"
            ) from exc

        if not response.choices:
            raise ProviderUnavailable(
                f"Grok returned no choices for {call.document_id!r}"
            )

        message = response.choices[0].message
        tool_calls = message.tool_calls
        if not tool_calls:
            raise MalformedProviderResponse(
                f"Grok did not call {TOOL_NAME!r} for {call.document_id!r}; "
                f"got instead: {message.content!r}"
            )

        tool_call = tool_calls[0]
        # We only ever offer one function tool and force it via tool_choice, so this is
        # always the function variant in practice. Read it dynamically rather than with
        # `isinstance` against the SDK's concrete type, so a test double only needs to
        # shape-match the one attribute this code actually uses.
        function = getattr(tool_call, "function", None)
        if function is None:
            raise MalformedProviderResponse(
                f"Grok returned a non-function tool call for {call.document_id!r}: "
                f"{type(tool_call).__name__}"
            )

        arguments = function.arguments
        try:
            result: dict[str, Any] = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise MalformedProviderResponse(
                f"Grok's tool-call arguments for {call.document_id!r} are not valid JSON: {exc}"
            ) from exc
        return result


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

        if call.kind == "critique":
            # A critique call with no specific recording defaults to "no problem" —
            # every extraction test written before the critic existed still passes
            # without needing a matching critique response for each one.
            critique_key = f"{key}.critique"
            if critique_key in self.responses:
                return copy.deepcopy(self.responses[critique_key])
            return {"problem_found": False, "explanation": None}

        if key not in self.responses:
            raise MissingRecording(
                f"No recorded response for {call.document_id!r}. "
                f"Recorded: {sorted(self.responses)}. "
                "Record one, or run against a real provider."
            )
        # A copy, so a caller mutating the result cannot corrupt the store for later calls.
        return copy.deepcopy(self.responses[key])


class MissingRecording(LookupError):
    """No recorded response exists for this document."""
