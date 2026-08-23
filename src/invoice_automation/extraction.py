"""Extraction: a document becomes an invoice.

The model is constrained to the invoice schema rather than asked for JSON, and the result
is validated before anything downstream sees it. A schema violation is fed back to the
model as feedback and retried, bounded by a configured cap — the first self-correction
loop in the system. Repair of corrupted values and the extraction critic arrive with
ticket 06; this ticket only teaches the model to correct its own shape.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from .config import DEFAULT_EXTRACTION_MAX_ATTEMPTS
from .deps import Deps
from .documents import Document
from .models import Invoice
from .providers import MalformedProviderResponse, StructuredCall

SYSTEM_PROMPT = """\
You extract structured data from supplier invoices for an accounts-payable system.

Report what the document says. Do not correct, improve, or complete it:
- If a field is missing, leave it null. Never infer a value the document does not state.
- If a quantity is negative, report it negative.
- Preserve every line item separately, even when the same item appears more than once
  at different prices. Never merge or total them.
- Preserve item names exactly as written, including spacing.

Downstream stages handle repair, validation, and judgement. Your only job is fidelity to
the document.\
"""


class ExtractionFailed(Exception):
    """The document could not be turned into an invoice."""


def extract_invoice(document: Document, deps: Deps) -> Invoice:
    """Extract the invoice a document carries.

    On a schema violation, the validation error is fed back to the model as feedback and
    extraction retries, up to `DEFAULT_EXTRACTION_MAX_ATTEMPTS` attempts in total. Raises
    `ExtractionFailed` once that cap is exhausted.
    """
    schema = Invoice.model_json_schema()
    base_prompt = _user_prompt(document)
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
            payload: dict[str, Any] = deps.provider.structured(call)
            return Invoice.model_validate(payload)
        except (ValidationError, MalformedProviderResponse) as exc:
            last_error = exc
            feedback = (
                f"--- YOUR PREVIOUS ATTEMPT (#{attempt}) WAS REJECTED ---\n"
                f"{exc}\n"
                "Correct it and call the tool again with a payload matching the schema."
            )

    raise ExtractionFailed(
        f"{document.name}: no valid invoice after {DEFAULT_EXTRACTION_MAX_ATTEMPTS} "
        f"attempt(s): {last_error}"
    ) from last_error


def _user_prompt(document: Document) -> str:
    return (
        f"Extract the invoice from this {document.format.value} document "
        f"named {document.name}.\n\n"
        f"--- BEGIN DOCUMENT ---\n{document.raw_text}\n--- END DOCUMENT ---"
    )
