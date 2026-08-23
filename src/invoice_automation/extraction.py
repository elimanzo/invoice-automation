"""Extraction: a document becomes an invoice.

The model is constrained to the invoice schema rather than asked for JSON, and the result
is validated before anything downstream sees it. Schema-violation retries, repair of
corrupted values, and the extraction critic all arrive with tickets 03 and 06; this is the
straight path.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from .deps import Deps
from .documents import Document
from .models import Invoice
from .providers import StructuredCall

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

    Raises `ExtractionFailed` when the provider returns something that is not an invoice.
    """
    call = StructuredCall(
        system=SYSTEM_PROMPT,
        user=_user_prompt(document),
        schema=Invoice.model_json_schema(),
        document_id=document.name,
    )
    payload: dict[str, Any] = deps.provider.structured(call)

    try:
        return Invoice.model_validate(payload)
    except ValidationError as exc:
        raise ExtractionFailed(
            f"{document.name}: provider returned a payload that is not a valid invoice: {exc}"
        ) from exc


def _user_prompt(document: Document) -> str:
    return (
        f"Extract the invoice from this {document.format.value} document "
        f"named {document.name}.\n\n"
        f"--- BEGIN DOCUMENT ---\n{document.raw_text}\n--- END DOCUMENT ---"
    )
