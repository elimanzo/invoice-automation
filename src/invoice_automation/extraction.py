"""Extraction: a document becomes an invoice.

Three things happen here, in order, only for documents that reach this module at all —
structured formats bypass it entirely (ADR-0009):

1. **Repair** (`repair.py`) fixes the one unambiguous corruption pattern in the sample
   data — an OCR-style letter standing in for a digit inside a date or an amount — before
   the model ever sees the text. Every fix is recorded as a `Correction`.
2. **Extraction** asks the model for a schema-constrained invoice. A schema violation is
   fed back as feedback and retried, bounded by a configured cap — the self-correction
   loop from ticket 03.
3. **Critique** asks the model a second time, now reviewing its own extraction against
   the original document. A critique that finds a real problem is fed back the same way
   a schema violation is: as feedback, consuming one of the same bounded attempts.

Everything else this ticket calls "repair" — misspelled field labels, recognising
shipping as a charge rather than an item, leaving an ambiguous due date null — is taught
through the system prompt, not through rewriting text. Those are reading-comprehension
problems with more than one superficially plausible answer; that is what the model is
for, and a model competent enough to extract an invoice at all is competent enough to
read "Vndr:" as a vendor label without a lookup table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from .config import DEFAULT_EXTRACTION_MAX_ATTEMPTS
from .deps import Deps
from .documents import Document
from .models import Correction, Flag, FlagSeverity, Invoice
from .providers import MalformedProviderResponse, StructuredCall
from .repair import find_purchase_order_reference, find_unresolvable_due_date, repair_ocr_digits

SYSTEM_PROMPT = """\
You extract structured data from supplier invoices for an accounts-payable system.

Report what the document says. Do not correct, improve, or complete it:
- If a field is missing, omit it from your answer entirely, or use JSON null — never
  the text "null" as a string value. Never infer a value the document does not state.
- If a quantity is negative, report it negative.
- Preserve every line item separately, even when the same item appears more than once
  at different prices. Never merge or total them.
- Preserve item names exactly as written, including spacing.
- Documents abbreviate field labels inconsistently — "Vndr" is vendor, "Itms" is items,
  "Inv #" is the invoice number, "Dt"/"Due Dt" are the invoice and due dates, "Amt" is
  the total, "Pymnt Terms" is payment terms. Read the label by what it means, not its
  exact spelling.
- Freight, shipping, sales tax, and discount lines are charges, not line items — never
  add them to line_items. A genuine line item names a product the vendor is billing for.
- If the document cites a purchase order (e.g. "Ref PO-20260115"), capture it in
  purchase_order_reference.
- A due date must be a calendar date. If the document states something that isn't one —
  "yesterday", "ASAP", "TBD" — leave due_date null rather than compute or guess one.

Downstream stages handle validation and judgement beyond this. Your only job is fidelity
to the document.\
"""

CRITIC_SYSTEM_PROMPT = """\
You review an automated extraction of a supplier invoice against the original document
it was extracted from.

Report a problem only if something is actually wrong:
- A value in the extraction that contradicts the document, or names something (a
  vendor, an item, a figure) that appears nowhere in it.
- The vendor, or a line item the document clearly states, missing from the extraction.

Do NOT report a problem for:
- A null field, whenever the document simply doesn't state that value.
- currency being "USD" when the document uses a bare "$" with no currency code stated —
  that is the correct default, not an invented value, and is never worth reporting.

Say nothing is wrong unless you can point to a specific mismatch.\
"""


class ExtractionFailed(Exception):
    """The document could not be turned into an invoice."""


class _Critique(BaseModel):
    problem_found: bool
    explanation: str | None = None


@dataclass(frozen=True)
class ExtractionResult:
    """What extraction produces: the invoice, plus everything ticket 06 tracks
    alongside it — corrections made to the source text, and flags raised while reading
    it that validation has no way to discover on its own (it never sees raw text)."""

    invoice: Invoice
    corrections: list[Correction]
    flags: list[Flag]


def extract_invoice(document: Document, deps: Deps) -> ExtractionResult:
    """Extract the invoice a document carries.

    Retries on a schema violation or a critique finding a real problem, feeding either
    back as model feedback, up to `DEFAULT_EXTRACTION_MAX_ATTEMPTS` attempts in total.
    Raises `ExtractionFailed` once that cap is exhausted.
    """
    repaired = repair_ocr_digits(document.raw_text)
    working_text = repaired.text
    corrections = list(repaired.corrections)

    flags: list[Flag] = []
    unresolvable = find_unresolvable_due_date(working_text)
    if unresolvable is not None:
        flags.append(
            Flag(
                severity=FlagSeverity.INFO,
                code="due_date_unresolvable",
                message=(
                    f"Due date stated as {unresolvable!r}; no calendar date could be "
                    "derived from it"
                ),
            )
        )

    schema = Invoice.model_json_schema()
    base_prompt = _user_prompt(document, working_text)
    feedback: str | None = None
    last_error: Exception | None = None

    for attempt in range(1, DEFAULT_EXTRACTION_MAX_ATTEMPTS + 1):
        call = StructuredCall(
            system=SYSTEM_PROMPT,
            user=base_prompt if feedback is None else f"{base_prompt}\n\n{feedback}",
            schema=schema,
            document_id=document.name,
        )

        try:
            payload: dict[str, Any] = _strip_literal_nulls(deps.provider.structured(call))
            invoice = Invoice.model_validate(payload)
        except (ValidationError, MalformedProviderResponse) as exc:
            last_error = exc
            feedback = _retry_feedback(attempt, str(exc))
            continue

        critique = _critique(document, working_text, invoice, deps)
        if critique.problem_found:
            last_error = ExtractionFailed(critique.explanation or "the critic found a problem")
            feedback = _retry_feedback(attempt, critique.explanation or "no explanation given")
            continue

        return ExtractionResult(
            invoice=_with_purchase_order_reference(invoice, working_text),
            corrections=corrections,
            flags=flags,
        )

    raise ExtractionFailed(
        f"{document.name}: no valid invoice after {DEFAULT_EXTRACTION_MAX_ATTEMPTS} "
        f"attempt(s): {last_error}"
    ) from last_error


def _critique(document: Document, working_text: str, invoice: Invoice, deps: Deps) -> _Critique:
    call = StructuredCall(
        system=CRITIC_SYSTEM_PROMPT,
        user=(
            f"Original document ({document.format.value}):\n"
            f"--- BEGIN DOCUMENT ---\n{working_text}\n--- END DOCUMENT ---\n\n"
            f"Extraction to review:\n{invoice.model_dump_json(indent=2)}"
        ),
        schema=_Critique.model_json_schema(),
        document_id=document.name,
        kind="critique",
    )
    try:
        payload = _strip_literal_nulls(deps.provider.structured(call))
        return _Critique.model_validate(payload)
    except (ValidationError, MalformedProviderResponse):
        # A critic that can't produce a valid verdict shouldn't be able to block an
        # otherwise-good extraction — treat its own failure as "nothing to report"
        # rather than compounding one problem into two.
        return _Critique(problem_found=False, explanation=None)


def _with_purchase_order_reference(invoice: Invoice, working_text: str) -> Invoice:
    """Backstop the model's own reading with a deterministic regex, without overriding
    a value the model already found."""
    if invoice.purchase_order_reference is not None:
        return invoice
    reference = find_purchase_order_reference(working_text)
    if reference is None:
        return invoice
    return invoice.model_copy(update={"purchase_order_reference": reference})


def _strip_literal_nulls(payload: dict[str, Any]) -> dict[str, Any]:
    """Delete any key whose value is the literal string "null".

    Found live: told to "leave a field null", the model sometimes writes the text
    "null" as a string value instead of omitting the key or using JSON's null. Deleting
    the key restores the behaviour the prompt actually asked for — Pydantic applies each
    field's own default (or None, for an Optional field) only when the key is fully
    absent, not when it's present holding a stray string.
    """

    def _clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: _clean(v) for k, v in value.items() if not _is_literal_null(v)}
        if isinstance(value, list):
            return [_clean(v) for v in value]
        return value

    return _clean(payload)  # type: ignore[no-any-return]


def _is_literal_null(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() == "null"


def _retry_feedback(attempt: int, problem: str) -> str:
    return (
        f"--- YOUR PREVIOUS ATTEMPT (#{attempt}) WAS REJECTED ---\n"
        f"{problem}\n"
        "Correct it and call the tool again with a payload matching the schema."
    )


def _user_prompt(document: Document, working_text: str) -> str:
    return (
        f"Extract the invoice from this {document.format.value} document "
        f"named {document.name}.\n\n"
        f"--- BEGIN DOCUMENT ---\n{working_text}\n--- END DOCUMENT ---"
    )
