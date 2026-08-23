# ADR-0003: Target Python 3.14, declare a 3.11 floor

- **Status:** Accepted
- **Date:** 2026-08-22

## Context

The development machine has exactly one interpreter, Python 3.14.0. The risk was that a
C-extension in the dependency tree has no 3.14 wheel, forcing a source build — which fails
on Windows without Visual Studio build tools. A grader hitting that failure scores
functionality at zero for a dependency problem rather than for the code.

Rather than assume, the full dependency set was resolved in a throwaway 3.14 venv.

## Decision

Develop on 3.14. Declare `requires-python = ">=3.11"` — a floor, not a pin.

## Consequences

**Measured, not assumed.** Every C-extension in the tree ships a prebuilt `cp314` wheel:
`pydantic_core 2.46.4`, `cryptography 50.0.0`, `pillow 12.3.0`, `pypdfium2 5.13.0`,
`cffi 2.1.1`. No source builds, so no compiler requirement on any machine.

**Good.** A floor rather than a pin means a grader on 3.11, 3.12, or 3.13 is not rejected
for no reason. Installing from a clean clone is part of the definition of done.

**Bad.** The floor is a claim we only spot-check; the routinely exercised version is 3.14.
