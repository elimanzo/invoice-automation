"""The approval agent: rules first, an LLM allowed only to tighten them, read-only
tools it can call along the way, and the caution ratchet enforced in code regardless of
what any provider says.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from invoice_automation.deps import Deps
from invoice_automation.documents import load_document
from invoice_automation.graph import run_invoice
from invoice_automation.providers import AssistantTurn, StructuredCall, ToolCall


class _AgentScriptedProvider:
    """Implements both `structured()` (for extraction) and `converse()` (for the
    approval agent) — the two methods together satisfy both `Provider` and
    `ToolCallingProvider`, which is what unlocks the agent loop for these tests."""

    def __init__(
        self,
        extraction_payload: dict[str, Any],
        converse_turns: list[AssistantTurn] | None = None,
    ) -> None:
        self._extraction_payload = extraction_payload
        self._converse_turns = list(converse_turns or [])
        self.calls: list[StructuredCall] = []
        self.converse_calls: list[list[dict[str, Any]]] = []

    def structured(self, call: StructuredCall) -> dict[str, Any]:
        self.calls.append(call)
        if call.kind == "critique":
            return {"problem_found": False, "explanation": None}
        return self._extraction_payload

    def converse(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> AssistantTurn:
        self.converse_calls.append(messages)
        return self._converse_turns.pop(0)


def _document(tmp_path: Path, text: str = "irrelevant; the scripted provider ignores it") -> Any:
    path = tmp_path / "invoice_x.txt"
    path.write_text(text, encoding="utf-8")
    return load_document(path)


def _scoped(deps: Deps, provider: Any) -> Deps:
    return Deps(
        provider=provider,
        catalogue=deps.catalogue,
        payment=deps.payment,
        clock=deps.clock,
        registry=deps.registry,
    )


class TestFatalFlagsSkipTheAgentEntirely:
    def test_a_fatally_flagged_invoice_issues_no_agent_call(
        self, invoices_dir: Path, deps: Deps
    ) -> None:
        """invoice_1003.txt: Fraudster LLC, zero-stock FakeItem, urgency language.
        An empty converse_turns list means the test errors loudly if the code ever
        tries to call the agent — the strongest proof urgency 'did not help it' is
        that nothing capable of being persuaded ever saw it."""
        document = load_document(invoices_dir / "invoice_1003.txt")
        provider = _AgentScriptedProvider(
            extraction_payload={
                "invoice_number": "INV-1003",
                "vendor": {"name": "Fraudster LLC"},
                "line_items": [{"item": "FakeItem", "quantity": 100, "unit_price": "1000.00"}],
                "total": "100000.00",
                "payment_terms": "Immediate",
            },
            converse_turns=[],
        )
        scoped = _scoped(deps, provider)

        result = run_invoice(document, scoped)

        assert result.decision is not None
        assert result.decision.outcome == "rejected"
        assert provider.converse_calls == []
        assert result.tool_calls == []


class TestTheRatchet:
    def test_the_agent_can_escalate_an_invoice_the_rules_approved(
        self, tmp_path: Path, deps: Deps
    ) -> None:
        provider = _AgentScriptedProvider(
            extraction_payload={
                "vendor": {"name": "Widgets Inc."},
                "line_items": [{"item": "WidgetA", "quantity": 1, "unit_price": "250.00"}],
                "total": "250.00",
            },
            converse_turns=[
                AssistantTurn(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="1",
                            name="submit_assessment",
                            arguments={"outcome": "escalated", "reasoning": "looks off"},
                        )
                    ],
                )
            ],
        )
        scoped = _scoped(deps, provider)

        result = run_invoice(_document(tmp_path), scoped)

        assert result.decision is not None
        assert result.decision.outcome == "escalated"
        assert "looks off" in result.decision.reasoning

    def test_the_agent_cannot_approve_what_the_rules_escalated(
        self, tmp_path: Path, deps: Deps
    ) -> None:
        """A misbehaving or malicious provider tries to jump straight to 'approved'
        from an escalated base. Code clamps it — the schema's enum is the first line
        of defence, this is the one that holds even if a call ignores it."""
        provider = _AgentScriptedProvider(
            extraction_payload={
                "vendor": {"name": "Widgets Inc."},
                "line_items": [{"item": "WidgetA", "quantity": 1, "unit_price": "250.00"}],
                "total": "15000.00",  # over threshold: rules escalate
            },
            converse_turns=[
                AssistantTurn(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="1",
                            name="submit_assessment",
                            arguments={"outcome": "approved", "reasoning": "trust me"},
                        )
                    ],
                )
            ],
        )
        scoped = _scoped(deps, provider)

        result = run_invoice(_document(tmp_path), scoped)

        assert result.decision is not None
        assert result.decision.outcome == "escalated"  # not approved, despite the call
        assert result.payment is None

    def test_an_inconclusive_investigation_fails_closed_to_the_rule(
        self, tmp_path: Path, deps: Deps
    ) -> None:
        """The agent never calls submit_assessment and never says anything parseable
        either — the tool-call bound is exhausted. The rule's own decision must stand,
        not be guessed at."""
        from invoice_automation.config import APPROVAL_MAX_TOOL_CALLS

        provider = _AgentScriptedProvider(
            extraction_payload={
                "vendor": {"name": "Widgets Inc."},
                "line_items": [{"item": "WidgetA", "quantity": 1, "unit_price": "250.00"}],
                "total": "15000.00",
            },
            converse_turns=[
                AssistantTurn(content="I'm still thinking about it.", tool_calls=[])
                for _ in range(APPROVAL_MAX_TOOL_CALLS)
            ],
        )
        scoped = _scoped(deps, provider)

        result = run_invoice(_document(tmp_path), scoped)

        assert result.decision is not None
        assert result.decision.outcome == "escalated"  # the rule's own outcome, untouched


class TestReasoningIsAlwaysRecorded:
    def test_the_final_reasoning_always_includes_the_rules_own_reasoning(
        self, tmp_path: Path, deps: Deps
    ) -> None:
        provider = _AgentScriptedProvider(
            extraction_payload={
                "vendor": {"name": "Widgets Inc."},
                "line_items": [{"item": "WidgetA", "quantity": 1, "unit_price": "250.00"}],
                "total": "250.00",
            },
            converse_turns=[
                AssistantTurn(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="1",
                            name="submit_assessment",
                            arguments={"outcome": "approved", "reasoning": "confirmed clean"},
                        )
                    ],
                )
            ],
        )
        scoped = _scoped(deps, provider)

        result = run_invoice(_document(tmp_path), scoped)

        assert result.decision is not None
        assert "scrutiny threshold" in result.decision.reasoning  # the rule's own text
        assert "confirmed clean" in result.decision.reasoning  # the agent's own text


class TestVendorIdentityChangeIsNoted:
    def test_a_noted_vendor_rename_flows_through_to_the_final_reasoning(
        self, tmp_path: Path, deps: Deps
    ) -> None:
        """Mirrors INV-1012's 'formerly FastShip Ltd.' case: this proves the mechanism
        carries the agent's observation through to the recorded decision, not that a
        real model reliably notices it (verified separately, live)."""
        provider = _AgentScriptedProvider(
            extraction_payload={
                "vendor": {"name": "QuickShip Distributers"},
                "line_items": [{"item": "WidgetA", "quantity": 1, "unit_price": "250.00"}],
                "total": "250.00",
            },
            converse_turns=[
                AssistantTurn(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="1",
                            name="submit_assessment",
                            arguments={
                                "outcome": "approved",
                                "reasoning": (
                                    "vendor is styled as formerly FastShip Ltd.; "
                                    "noted, not otherwise concerning"
                                ),
                            },
                        )
                    ],
                )
            ],
        )
        scoped = _scoped(deps, provider)

        result = run_invoice(_document(tmp_path), scoped)

        assert result.decision is not None
        assert "formerly FastShip" in result.decision.reasoning


class TestDecisionsAreStableAcrossRuns:
    def test_the_fake_provider_produces_an_identical_decision_every_run(
        self, invoices_dir: Path, deps: Deps
    ) -> None:
        document = load_document(invoices_dir / "invoice_1001.txt")

        first = run_invoice(document, deps)
        second = run_invoice(document, deps)

        assert first.decision is not None and second.decision is not None
        assert first.decision.outcome == second.decision.outcome == "approved"
        assert first.decision.reasoning == second.decision.reasoning
