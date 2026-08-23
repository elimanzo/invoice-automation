# ADR-0007: Fuzzy matching may suggest, never substitute

- **Status:** Accepted
- **Date:** 2026-08-22

## Context

Item names arrive corrupted. INV-1012 bills `Widget A` and `Gadget X` with inserted spaces;
those are real catalogue items and must match. INV-1016 bills `WidgetC` and INV-1008 bills
`SuperGizmo` and `MegaSprocket`; those are not catalogue items and must not match anything.

Similarity was measured with `difflib.SequenceMatcher` over normalised names rather than
assumed:

| Invoice text | Exact after normalise | Nearest catalogue | Ratio |
| -------------- | --------------------- | ----------------- | --------- |
| `Widget A` | **WidgetA** | — | 1.000 |
| `Gadget X` | **GadgetX** | — | 1.000 |
| `WidgetC` | none | WidgetA | **0.857** |
| `SuperGizmo` | none | FakeItem | 0.333 |
| `MegaSprocket` | none | GadgetX | 0.421 |

Two findings. Normalisation alone resolves every legitimate variant in the data at 1.000.
And `WidgetC` scores 0.857 against a real item — so the conventional 0.85 fuzzy threshold
would silently match an uncatalogued product to WidgetA and wave through a $1,050 line.

## Decision

1. Normalise (lowercase, strip non-alphanumerics) and match exactly. A hit is a match.
2. A miss stays **unknown**. Fuzzy runs only to name the nearest catalogue entry, and that
   suggestion goes into the flag text — never into the matching decision.

`WidgetC` therefore yields: *unknown item 'WidgetC' — not in inventory; nearest catalogue
entry 'WidgetA' (86% similar), not auto-matched.*

## Consequences

**Good.** Validation outcomes are identical to strict normalisation, so the fraud path stays
closed, while reviewers and the approval critic still get the "probably a typo for WidgetA"
signal. Costs about 15 lines using the standard library; no dependency.

**Bad.** A genuinely misspelled legitimate item is flagged unknown and reaches a human
rather than being auto-corrected. Deliberate: inventing a match on an unknown item is the
exact failure this control exists to prevent.
