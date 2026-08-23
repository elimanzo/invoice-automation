"""The eval harness: scores the pipeline against the golden set, reported as
percentages rather than asserted line by line, so a prompt or provider change surfaces
as "94% to 81%" rather than one opaque test failure.

Two kinds of document live in the golden set (`data/invoices/` plus the authored
`data/invoices_extra/`), and they're treated differently:

- **Scored** — a document whose correct decision (and, where extraction actually
  matters, correct fields) is known *externally*: the scenarios REQUIREMENTS.md itself
  names, and the four authored fixtures, whose ground truth is known by construction
  because we wrote them. `GOLDEN_CASES` records that ground truth as data.
- **Unscored** — everything else in the provided data. It is still processed and
  reported (a batch run, per batch.py, never skips a document), but no expectation is
  invented for it: doing so would measure the system's agreement with itself, not with
  anything real, and would overstate coverage.

`run_eval` drives the real pipeline through `batch.run_batch` — the same code path a
clerk's `--invoice_dir` run takes — so a scoring run and a production run can never
silently diverge in behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver

from .batch import BatchItem, run_batch
from .deps import Deps

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_INVOICES_DIR = REPO_ROOT / "data" / "invoices"
GOLDEN_EXTRA_DIR = REPO_ROOT / "data" / "invoices_extra"


@dataclass(frozen=True)
class FieldExpectation:
    """Ground truth for the handful of header fields worth scoring. A field left
    `None` is simply not checked for this case — most cases care about the decision,
    not every field on the invoice."""

    invoice_number: str | None = None
    vendor_name: str | None = None
    total: str | None = None  # Compared as Decimal("...") == Decimal(actual).
    currency: str | None = None


@dataclass(frozen=True)
class GoldenCase:
    """One document's expected outcome. Data, not an assertion — see the module
    docstring. `expected_decision` of `None` means this case is scored on fields only."""

    document_name: str
    expected_decision: str | None
    expected_fields: FieldExpectation | None = None
    expected_error: str | None = None
    """A substring the raised error must contain, for a document expected to fail
    outright rather than reach a decision (e.g. a revision arriving after payment).
    Mutually exclusive with `expected_decision` in practice, though nothing enforces
    that beyond the cases below being written one way or the other."""
    note: str = ""


# The brief's own scenario table (REQUIREMENTS.md) plus the four authored fixtures
# (data/invoices_extra/), whose ground truth is known by construction. Every case here
# is a deterministically-parsed document (JSON/XML, per ADR-0009) precisely so the
# expectation is exact rather than a best guess at what a model "should" extract.
GOLDEN_CASES: tuple[GoldenCase, ...] = (
    GoldenCase(
        document_name="invoice_1004.json",
        expected_decision="approved",
        expected_fields=FieldExpectation(
            invoice_number="INV-1004", vendor_name="Precision Parts Ltd.",
            total="1890.00", currency="USD",
        ),
        note="REQUIREMENTS.md: normal order within stock",
    ),
    GoldenCase(
        document_name="invoice_1004_revised.json",
        expected_decision=None,
        expected_error="revised",
        note=(
            "invoice_1004.json pays instantly (clean, under threshold), so by the "
            "time the revision arrives the obligation is already settled — a human "
            "must decide, not this code (reconciliation.RevisionAfterPayment)"
        ),
    ),
    GoldenCase(
        document_name="invoice_1009.json",
        expected_decision="rejected",
        expected_fields=FieldExpectation(invoice_number="INV-1009", vendor_name="", currency="USD"),
        note="REQUIREMENTS.md: invalid data (negative quantity, empty vendor)",
    ),
    GoldenCase(
        document_name="invoice_1013.json",
        expected_decision="rejected",
        expected_fields=FieldExpectation(invoice_number="INV-1013", total="22562.80"),
        note="WidgetA/WidgetB/GadgetX each exceed stock only once aggregated across lines",
    ),
    GoldenCase(
        document_name="invoice_1014.xml",
        expected_decision="approved",
        expected_fields=FieldExpectation(invoice_number="INV-1014", total="4125.00", currency="EUR"),
        note="non-USD total converts to well under the scrutiny threshold",
    ),
    GoldenCase(
        document_name="invoice_1016.json",
        expected_decision="approved",
        expected_fields=FieldExpectation(
            invoice_number="INV-1016", vendor_name="Widgets Inc.", total="3233.00"
        ),
        note="REQUIREMENTS.md: item not in database (WidgetC), matched to nothing",
    ),
    GoldenCase(
        document_name="invoice_9001_padded_price.json",
        expected_decision="approved",
        expected_fields=FieldExpectation(invoice_number="INV-9001", total="1200.00"),
        note="authored: padded price, caught by the price check and nothing else",
    ),
    GoldenCase(
        document_name="invoice_9002_threshold_under.json",
        expected_decision="approved",
        expected_fields=FieldExpectation(invoice_number="INV-9002", total="9999.00"),
        note="authored: threshold-evasion pair, $1 under",
    ),
    GoldenCase(
        document_name="invoice_9003_threshold_over.json",
        expected_decision="escalated",
        expected_fields=FieldExpectation(invoice_number="INV-9003", total="10001.00"),
        note="authored: threshold-evasion pair, $1 over",
    ),
    # The "a_"/"b_" filename prefixes are load-bearing: run_batch (batch.py) processes
    # a directory's files in lexicographic order, and reconciliation.py treats
    # whichever document it sees first for an identity as the baseline the second is
    # compared against. Renaming either file, or adding a third INV-9005 document that
    # sorts earlier, would flip which one is "the original" and these two expectations
    # would need to flip with it.
    GoldenCase(
        document_name="invoice_9005_a_original.json",
        expected_decision="approved",
        expected_fields=FieldExpectation(invoice_number="INV-9005", total="250.00"),
        note="authored: contradicting-duplicate pair, the original",
    ),
    GoldenCase(
        document_name="invoice_9005_b_contradiction.json",
        expected_decision="escalated",
        note="authored: contradicting-duplicate pair, the contradiction — hard-flagged",
    ),
    GoldenCase(
        document_name="invoice_9004_image_only.pdf",
        expected_decision=None,
        expected_error="image-only PDF",
        note="authored: no text layer at all, fails extraction outright rather than reaching a decision",
    ),
)

_GOLDEN_CASES_BY_NAME: dict[str, GoldenCase] = {c.document_name: c for c in GOLDEN_CASES}


@dataclass(frozen=True)
class FieldScore:
    field: str
    expected: str
    actual: str | None
    matched: bool


@dataclass(frozen=True)
class CaseResult:
    """One scored document's outcome, detailed enough to see exactly what disagreed."""

    document_name: str
    error: str | None
    decision_expected: str | None
    decision_actual: str | None
    decision_matched: bool | None
    field_scores: tuple[FieldScore, ...]
    note: str


@dataclass(frozen=True)
class EvalReport:
    """The whole run: every scored case in detail, plus the documents that were
    processed but never scored, so coverage is never overstated."""

    total_documents: int
    scored: tuple[CaseResult, ...]
    unscored_documents: tuple[str, ...]

    @property
    def decision_agreement_pct(self) -> float | None:
        judged = [c for c in self.scored if c.decision_matched is not None]
        if not judged:
            return None
        return 100.0 * sum(1 for c in judged if c.decision_matched) / len(judged)

    @property
    def field_accuracy_pct(self) -> float | None:
        all_fields = [fs for c in self.scored for fs in c.field_scores]
        if not all_fields:
            return None
        return 100.0 * sum(1 for fs in all_fields if fs.matched) / len(all_fields)

    def summary(self) -> str:
        decision_pct = self.decision_agreement_pct
        field_pct = self.field_accuracy_pct
        decision_text = f"{decision_pct:.1f}%" if decision_pct is not None else "n/a"
        field_text = f"{field_pct:.1f}%" if field_pct is not None else "n/a"
        return (
            f"{len(self.scored)}/{self.total_documents} documents scored "
            f"({len(self.unscored_documents)} processed but unscored); "
            f"decision agreement {decision_text}; field accuracy {field_text}"
        )


def run_eval(
    deps: Deps,
    *,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    invoices_dir: Path = GOLDEN_INVOICES_DIR,
    extra_dir: Path = GOLDEN_EXTRA_DIR,
) -> EvalReport:
    """Run the whole golden set through the real pipeline and score what has a known
    answer. `invoices_dir`/`extra_dir` are overridable so tests can point this at a
    smaller, controlled directory without touching the real golden set."""
    items: dict[str, BatchItem] = {}
    for directory in (invoices_dir, extra_dir):
        batch = run_batch(directory, deps, checkpointer=checkpointer)
        for item in batch.items:
            items[item.document_name] = item

    scored: list[CaseResult] = []
    unscored: list[str] = []
    for name, item in items.items():
        case = _GOLDEN_CASES_BY_NAME.get(name)
        if case is None:
            unscored.append(name)
            continue
        scored.append(_score_case(case, item))

    return EvalReport(
        total_documents=len(items),
        scored=tuple(scored),
        unscored_documents=tuple(sorted(unscored)),
    )


def _score_case(case: GoldenCase, item: BatchItem) -> CaseResult:
    if case.expected_error is not None:
        matched = item.error is not None and case.expected_error in item.error
        return CaseResult(
            document_name=case.document_name,
            error=item.error,
            decision_expected=None,
            decision_actual=None,
            decision_matched=matched,
            field_scores=(),
            note=case.note,
        )

    if item.error is not None or item.result is None:
        decision_matched = False if case.expected_decision is not None else None
        return CaseResult(
            document_name=case.document_name,
            error=item.error or "no result",
            decision_expected=case.expected_decision,
            decision_actual=None,
            decision_matched=decision_matched,
            field_scores=(),
            note=case.note,
        )

    decision_actual = item.result.decision.outcome if item.result.decision else None
    decision_matched = (
        decision_actual == case.expected_decision if case.expected_decision is not None else None
    )

    field_scores: list[FieldScore] = []
    invoice = item.result.invoice
    if case.expected_fields is not None and invoice is not None:
        expected = case.expected_fields
        if expected.invoice_number is not None:
            field_scores.append(
                _field("invoice_number", expected.invoice_number, invoice.invoice_number)
            )
        if expected.vendor_name is not None:
            field_scores.append(_field("vendor_name", expected.vendor_name, invoice.vendor.name))
        if expected.total is not None:
            actual_total = str(invoice.total) if invoice.total is not None else None
            field_scores.append(_field("total", expected.total, actual_total, numeric=True))
        if expected.currency is not None:
            field_scores.append(_field("currency", expected.currency, invoice.currency))

    return CaseResult(
        document_name=case.document_name,
        error=None,
        decision_expected=case.expected_decision,
        decision_actual=decision_actual,
        decision_matched=decision_matched,
        field_scores=tuple(field_scores),
        note=case.note,
    )


def _field(name: str, expected: str, actual: str | None, *, numeric: bool = False) -> FieldScore:
    matched = actual == expected
    if numeric and not matched and actual is not None:
        # Numeric fields (total) may differ only in trailing-zero formatting
        # ("250" vs "250.00") without disagreeing in value. Scoped to numeric fields
        # only — an identifier like invoice_number must match exactly, since
        # Decimal("1004") == Decimal("1004.0") would otherwise mask a real
        # extraction discrepancy (e.g. a dropped leading zero).
        try:
            matched = Decimal(actual) == Decimal(expected)
        except InvalidOperation:
            matched = False
    return FieldScore(field=name, expected=expected, actual=actual, matched=matched)
