# ADR-0002: LangGraph for orchestration

- **Status:** Accepted
- **Date:** 2026-08-22

## Context

The workflow is four stages, but not a straight line. It needs conditional branching (a
fatal flag skips approval and goes straight to reject), cycles (the critique loops in
ingestion and approval), a shared state object, live per-stage events for the dashboard, and
the ability to suspend mid-graph while a human reviews an escalation (ADR-0005).

Options considered: LangGraph, a hand-rolled async orchestrator, CrewAI.

Dependency resolution was verified on Python 3.14 before choosing: `langgraph 1.2.11` and
its transitive tree install as prebuilt `cp314` wheels, so this decision costs no source
builds (ADR-0003).

## Decision

LangGraph. Nodes are plain functions over one Pydantic state object; conditional edges
express branching; backward edges express the critique loops; the SQLite checkpointer
persists state after every node.

## Consequences

**Good.** Four things we would otherwise build by hand come free: conditional routing,
cycles, state persistence, and event streaming for the dashboard. The checkpointer is also
what makes interrupt-and-resume (ADR-0005) nearly free, and what backs the run traces.
`graph.get_graph().draw_mermaid()` generates the README architecture diagram from the code
that actually runs, so the diagram cannot drift from the implementation.

**Bad.** A framework dependency with its own abstractions. Orchestration logic needs
LangGraph familiarity to read fully, where 200 lines of hand-rolled asyncio would not.

**Rejected.** A custom orchestrator is honestly adequate for four sequential steps, but the
loops plus persistence plus streaming is where it stops being adequate, and that plumbing
would be built at the expense of the dashboard and the dirty-data handling that actually
differentiate this submission. CrewAI models agents as conversing roles, which resists the
conditional control flow this workflow is mostly made of.
