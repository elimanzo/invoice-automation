# ADR-0008: React front end with a committed bundle

- **Status:** Accepted
- **Date:** 2026-08-22

## Context

"Users will understand and enjoy using this system" is a graded criterion, and the dashboard
carries it: three views (pipeline, review queue, ledger) plus a run drill-down, with live
updates as invoices flow through stages.

The competing concern is the grader's first five minutes. Any `npm install` step is a chance
to fail on someone else's machine, and it dilutes a Python submission. That argued for
server-rendered Jinja templates with htmx — no build step at all.

The deciding factor is who writes the code: this author is fluent in React and not in Jinja,
and interface quality tracks fluency.

## Decision

React + Vite, with the production bundle built and **committed** to the repository. FastAPI
serves it as static files. Live updates use server-sent events.

The grader runs `pip install -e . && python -m invoice_automation.web`. Node is never
required to *run* the system, only to rebuild the front end.

## Consequences

**Good.** Zero install friction — identical to the htmx option from the grader's side. The
UI is built in the tool the author is actually productive in, which is where the UI/UX
criterion is won.

**Bad.** Committed build artifacts are unusual in a production repo and will show as large
diffs. Justified explicitly as grader convenience; `npm run build` is documented for
rebuilding, and a stale bundle is a real failure mode to check before submitting.

**Rejected.** Jinja + htmx remains the better choice for an author without React fluency, or
for a single-screen dashboard. Neither applies.
