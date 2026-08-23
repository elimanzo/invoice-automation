"""Ticket 17: the eval harness that scores the pipeline against the golden set.

Every case in `evals.GOLDEN_CASES` is a deterministically-parsed document, so the
expectations here don't depend on a provider at all — they hold regardless of which
provider `deps` carries. That keeps this suite honest about what it's testing: the
scoring mechanism, not model accuracy (cassette-backed model scoring is ticket 18).
"""

from __future__ import annotations

import pytest

from invoice_automation import evals
from invoice_automation.deps import Deps
from invoice_automation.evals import GOLDEN_CASES, run_eval


def test_golden_cases_are_scored_and_everything_else_is_not(deps: Deps) -> None:
    report = run_eval(deps)

    scored_names = {c.document_name for c in report.scored}
    assert scored_names == {c.document_name for c in GOLDEN_CASES}
    assert report.total_documents > len(GOLDEN_CASES)
    assert report.unscored_documents  # the remaining provided invoices
    assert set(report.unscored_documents).isdisjoint(scored_names)
    # The real sample data is processed too, not skipped — it just isn't scored.
    assert "invoice_1006.csv" in report.unscored_documents


def test_report_agrees_perfectly_on_the_deterministic_golden_set(deps: Deps) -> None:
    """Nothing in `GOLDEN_CASES` needs a provider (all are JSON/XML, parsed
    deterministically), so the pipeline's answer and the recorded ground truth must
    match exactly — this is what "known by construction" means in practice."""
    report = run_eval(deps)

    assert report.decision_agreement_pct == 100.0
    assert report.field_accuracy_pct == 100.0
    # invoice_1004_revised.json (a revision after payment) and invoice_9004_image_only.pdf
    # (no text layer) are expected to raise rather than reach a decision — every other
    # scored case reaches one cleanly.
    erroring = {c.document_name for c in report.scored if c.error is not None}
    assert erroring == {"invoice_1004_revised.json", "invoice_9004_image_only.pdf"}


def test_summary_reports_coverage_and_both_percentages(deps: Deps) -> None:
    report = run_eval(deps)

    text = report.summary()
    assert f"{len(report.scored)}/{report.total_documents}" in text
    assert "decision agreement" in text
    assert "field accuracy" in text


def test_a_wrong_expectation_surfaces_as_a_disagreement_not_a_silent_pass(
    deps: Deps, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scoring mechanism must be able to fail, or it isn't measuring anything.
    Point one case at a decision the pipeline will never actually reach and confirm
    the report says so rather than passing anyway."""
    wrong_case = evals.GoldenCase(document_name="invoice_1009.json", expected_decision="approved")
    monkeypatch.setitem(evals._GOLDEN_CASES_BY_NAME, "invoice_1009.json", wrong_case)

    report = run_eval(deps)

    case = next(c for c in report.scored if c.document_name == "invoice_1009.json")
    assert case.decision_matched is False
    assert report.decision_agreement_pct is not None
    assert report.decision_agreement_pct < 100.0
