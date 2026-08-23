"""The read-only tools the approval agent can call: correctness, and the property that
matters most — none of them can write anything."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from invoice_automation.deps import Deps
from invoice_automation.documents import load_document
from invoice_automation.graph import run_invoice
from invoice_automation.providers import AssistantTurn, StructuredCall, ToolCall
from invoice_automation.tools import TOOL_NAMES, dispatch

# One valid argument set per tool, by name — used to exercise every tool generically
# rather than writing five near-identical call sites.
_SAMPLE_ARGUMENTS: dict[str, dict[str, Any]] = {
    "lookup_item": {"item": "WidgetA"},
    "check_stock": {"item": "WidgetA"},
    "vendor_history": {"vendor": "Widgets Inc."},
    "prior_invoices": {"vendor": "Widgets Inc."},
    "fx_rate": {"currency": "EUR"},
}


def test_every_declared_tool_has_a_sample_argument_set() -> None:
    """A guard on the test itself: if a tool is added without updating the sample
    arguments above, this fails loudly instead of the write-safety test silently
    skipping the new tool."""
    assert set(_SAMPLE_ARGUMENTS) == TOOL_NAMES


def test_no_tool_call_mutates_catalogue_or_registry_state(deps: Deps) -> None:
    before_items = deps.catalogue.all_items()
    before_vendor_check = deps.catalogue.is_known_vendor("Widgets Inc.")
    before_payments = deps.registry.payments()

    for name, arguments in _SAMPLE_ARGUMENTS.items():
        dispatch(name, arguments, deps)

    assert deps.catalogue.all_items() == before_items
    assert deps.catalogue.is_known_vendor("Widgets Inc.") == before_vendor_check
    assert deps.registry.payments() == before_payments


def test_lookup_item_reports_a_real_catalogue_entry(deps: Deps) -> None:
    result = dispatch("lookup_item", {"item": "WidgetA"}, deps)

    assert result == {"found": True, "stock": 15, "expected_unit_price": "250.00"}


def test_lookup_item_reports_absence_rather_than_guessing(deps: Deps) -> None:
    result = dispatch("lookup_item", {"item": "NoSuchThing"}, deps)

    assert result == {"found": False}


def test_lookup_item_and_check_stock_normalise_the_same_way_validation_does(
    deps: Deps,
) -> None:
    """Found live: an exact-match lookup told the agent 'Widget A' (as written in a
    real corrupted invoice) doesn't exist, and it escalated on that false premise, while
    validation.py's normalised match correctly recognised it as WidgetA. Both code paths
    must agree, always — that's the whole reason match_item is a single shared function."""
    spaced = dispatch("lookup_item", {"item": "Widget A"}, deps)
    exact = dispatch("lookup_item", {"item": "WidgetA"}, deps)

    assert spaced == exact == {"found": True, "stock": 15, "expected_unit_price": "250.00"}
    assert dispatch("check_stock", {"item": "Widget A"}, deps) == {"stock": 15}


def test_vendor_history_reports_known_and_unknown_vendors(deps: Deps) -> None:
    known = dispatch("vendor_history", {"vendor": "Widgets Inc."}, deps)
    unknown = dispatch("vendor_history", {"vendor": "Fraudster LLC"}, deps)

    assert known["known_vendor"] is True
    assert unknown["known_vendor"] is False


def test_fx_rate_reports_the_configured_rate(deps: Deps) -> None:
    result = dispatch("fx_rate", {"currency": "EUR"}, deps)

    assert result["usd_per_unit"] == "1.08"


def test_fx_rate_reports_none_for_an_unconfigured_currency(deps: Deps) -> None:
    result = dispatch("fx_rate", {"currency": "JPY"}, deps)

    assert result["usd_per_unit"] is None


class _AgentScriptedProvider:
    def __init__(
        self, extraction_payload: dict[str, Any], converse_turns: list[AssistantTurn]
    ) -> None:
        self._extraction_payload = extraction_payload
        self._converse_turns = list(converse_turns)
        self.calls: list[StructuredCall] = []

    def structured(self, call: StructuredCall) -> dict[str, Any]:
        self.calls.append(call)
        if call.kind == "critique":
            return {"problem_found": False, "explanation": None}
        return self._extraction_payload

    def converse(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AssistantTurn:
        return self._converse_turns.pop(0)


def test_an_unfamiliar_vendor_causes_the_agent_to_call_vendor_history(
    invoices_dir: Path, deps: Deps
) -> None:
    """invoice_1008.txt: NoProd Industries, not in the vendor master."""
    document = load_document(invoices_dir / "invoice_1008.txt")
    provider = _AgentScriptedProvider(
        extraction_payload={
            "vendor": {"name": "NoProd Industries"},
            "line_items": [
                {"item": "SuperGizmo", "quantity": 12, "unit_price": "400.00"},
                {"item": "MegaSprocket", "quantity": 6, "unit_price": "850.00"},
            ],
            "total": "9900.00",
        },
        converse_turns=[
            AssistantTurn(
                content=None,
                tool_calls=[
                    ToolCall(id="1", name="vendor_history", arguments={"vendor": "NoProd Industries"})
                ],
            ),
            AssistantTurn(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="2",
                        name="submit_assessment",
                        arguments={
                            "outcome": "escalated",
                            "reasoning": "vendor has no payment history",
                        },
                    )
                ],
            ),
        ],
    )
    scoped = Deps(
        provider=provider,
        catalogue=deps.catalogue,
        payment=deps.payment,
        clock=deps.clock,
        registry=deps.registry,
    )

    result = run_invoice(document, scoped)

    assert any(tc.name == "vendor_history" for tc in result.tool_calls)
    call = next(tc for tc in result.tool_calls if tc.name == "vendor_history")
    assert call.arguments == {"vendor": "NoProd Industries"}
    assert call.result["known_vendor"] is False
    assert result.decision is not None
    assert result.decision.outcome == "escalated"
