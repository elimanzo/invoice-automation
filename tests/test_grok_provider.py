"""GrokProvider: parsing the openai SDK's tool-call shape into a plain dict.

No network and no key anywhere here — the client is a hand-built stand-in exposing only
the attributes `structured()` actually reads, so these tests exercise the real parsing
logic without depending on the real SDK's object graph.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from invoice_automation.providers import (
    TOOL_NAME,
    GrokProvider,
    MalformedProviderResponse,
    StructuredCall,
)


class _FakeClient:
    """Stands in for `openai.OpenAI`: only `.chat.completions.create(...)` is used."""

    def __init__(self, response: Any) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    class _Chat:
        def __init__(self, outer: "_FakeClient") -> None:
            self._outer = outer

        class _Completions:
            def __init__(self, outer: "_FakeClient") -> None:
                self._outer = outer

            def create(self, **kwargs: Any) -> Any:
                self._outer.calls.append(kwargs)
                return self._outer._response

        @property
        def completions(self) -> "_FakeClient._Chat._Completions":
            return _FakeClient._Chat._Completions(self._outer)

    @property
    def chat(self) -> "_FakeClient._Chat":
        return _FakeClient._Chat(self)


def _tool_call_response(arguments: str) -> Any:
    tool_call = SimpleNamespace(function=SimpleNamespace(arguments=arguments))
    message = SimpleNamespace(tool_calls=[tool_call], content=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _no_tool_call_response(content: str) -> Any:
    message = SimpleNamespace(tool_calls=None, content=content)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _call() -> StructuredCall:
    return StructuredCall(system="sys", user="user", schema={"type": "object"}, document_id="x.txt")


def test_a_well_formed_tool_call_is_parsed_into_a_dict() -> None:
    client = _FakeClient(_tool_call_response(json.dumps({"vendor": {"name": "Acme"}})))
    provider = GrokProvider(api_key="k", base_url="https://api.x.ai/v1", model="grok-4", client=client)  # type: ignore[arg-type]

    result = provider.structured(_call())

    assert result == {"vendor": {"name": "Acme"}}


def test_the_call_forces_the_tool_and_names_the_model() -> None:
    client = _FakeClient(_tool_call_response("{}"))
    provider = GrokProvider(api_key="k", base_url="https://api.x.ai/v1", model="grok-4", client=client)  # type: ignore[arg-type]

    provider.structured(_call())

    kwargs = client.calls[0]
    assert kwargs["model"] == "grok-4"
    assert kwargs["tool_choice"] == {"type": "function", "function": {"name": TOOL_NAME}}
    assert kwargs["tools"][0]["function"]["name"] == TOOL_NAME
    assert kwargs["tools"][0]["function"]["parameters"] == {"type": "object"}


def test_no_tool_call_is_a_malformed_response() -> None:
    client = _FakeClient(_no_tool_call_response("I'd rather just describe it in prose."))
    provider = GrokProvider(api_key="k", base_url="https://api.x.ai/v1", model="grok-4", client=client)  # type: ignore[arg-type]

    with pytest.raises(MalformedProviderResponse) as excinfo:
        provider.structured(_call())

    assert "prose" in str(excinfo.value)


def test_unparseable_tool_arguments_are_a_malformed_response() -> None:
    client = _FakeClient(_tool_call_response("{not json"))
    provider = GrokProvider(api_key="k", base_url="https://api.x.ai/v1", model="grok-4", client=client)  # type: ignore[arg-type]

    with pytest.raises(MalformedProviderResponse):
        provider.structured(_call())
