"""Deterministic repair of corrupted document text, ahead of model extraction.

Per CONTEXT.md: a correction is a record that the system stored something different
from what a document literally said. This module produces exactly one kind of
correction — repairing an OCR-style letter/digit substitution ('O' standing in for '0')
inside a date or a currency amount — because that pattern is unambiguous by construction:
within a token that is otherwise all digits, a stray letter O can only ever mean zero.

Everything else this ticket calls "repair" (misspelled field labels, non-item charges,
where a purchase order reference lives) is handled by giving the model better
instructions, not by rewriting text — those aren't ambiguous substitutions with one
correct answer, they're reading comprehension, which is what the model is for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from .models import Correction

# High confidence: within a token built from digits and 'O'/'o', the letter can only
# stand for zero. There is no second interpretation to be uncertain about.
_OCR_DIGIT_CONFIDENCE = 0.95

# A date written DD-Mon-YYYY, e.g. "26-Jan-2O26". The corruption lands in the year.
_DATE_TOKEN = re.compile(r"\b\d{1,2}-[A-Za-z]{3}-[0-9OoI]{2,4}\b")

# A currency amount, e.g. "$3,500.O0". The corruption can land anywhere in the digits.
_MONEY_TOKEN = re.compile(r"\$[0-9OoI,]+\.[0-9OoI]{2}\b")

_OCR_SUBSTITUTIONS = str.maketrans({"O": "0", "o": "0", "I": "1"})


@dataclass(frozen=True)
class RepairResult:
    """The text a repair pass produced, and the audit trail for what it changed."""

    text: str
    corrections: list[Correction]


def repair_ocr_digits(raw_text: str) -> RepairResult:
    """Fix letter-for-digit OCR substitutions inside date and currency tokens.

    Only tokens that already look numeric are touched — a stray 'O' or 'I' anywhere
    else in the document (a word, a name) is left alone. A candidate fix that doesn't
    produce a plausible date or amount is not applied: the original text is kept, and
    the correction records that low confidence instead of guessing.
    """
    corrections: list[Correction] = []
    text = raw_text

    for pattern, kind in ((_DATE_TOKEN, "date"), (_MONEY_TOKEN, "amount")):
        text, found = _repair_tokens(text, pattern, kind)
        corrections.extend(found)

    return RepairResult(text=text, corrections=corrections)


def _repair_tokens(
    text: str, pattern: re.Pattern[str], kind: str
) -> tuple[str, list[Correction]]:
    corrections: list[Correction] = []

    def _replace(match: re.Match[str]) -> str:
        raw_token = match.group(0)
        if not any(c in raw_token for c in "OoI"):
            return raw_token  # Nothing corrupted in this particular match.

        fixed_token = raw_token.translate(_OCR_SUBSTITUTIONS)
        valid = _looks_like_a_date(fixed_token) if kind == "date" else _looks_like_money(
            fixed_token
        )

        if valid:
            corrections.append(
                Correction(
                    field=f"raw_text:{kind}",
                    raw=raw_token,
                    value=fixed_token,
                    reason="OCR letter/digit substitution: 'O'/'I' read as a digit",
                    confidence=_OCR_DIGIT_CONFIDENCE,
                )
            )
            return fixed_token

        # The substitution didn't produce anything plausible — don't guess. Record
        # what was attempted, at low confidence, and leave the original text in place
        # for the model (and the critic) to deal with.
        corrections.append(
            Correction(
                field=f"raw_text:{kind}",
                raw=raw_token,
                value=raw_token,
                reason=(
                    "looked like an OCR-corrupted "
                    f"{kind}, but the repaired form is not a plausible {kind}; left unrepaired"
                ),
                confidence=0.2,
            )
        )
        return raw_token

    return pattern.sub(_replace, text), corrections


def _looks_like_a_date(token: str) -> bool:
    """"26-Jan-2026" — day, three-letter month abbreviation, four-digit year."""
    try:
        datetime.strptime(token, "%d-%b-%Y")
    except ValueError:
        return False
    return True


def _looks_like_money(token: str) -> bool:
    try:
        Decimal(token.lstrip("$").replace(",", ""))
    except InvalidOperation:
        return False
    return True


# ---------------------------------------------------------------------------
# Detecting a relative or otherwise unresolvable due date the model correctly
# declined to invent. This runs on the raw text, independent of what extraction
# returned — a due date being null is only worth flagging when the document actually
# stated *something* the model couldn't resolve.
# ---------------------------------------------------------------------------

_UNRESOLVABLE_DUE_DATE_WORDS = {
    "yesterday",
    "today",
    "tomorrow",
    "immediately",
    "asap",
    "n/a",
    "tbd",
}

_DUE_DATE_LINE = re.compile(r"(?im)^\s*due\s*(?:date)?\s*:\s*(.+?)\s*$")


def find_unresolvable_due_date(raw_text: str) -> str | None:
    """The literal phrase a document used for its due date, if it's one no calendar
    date can be derived from — e.g. "yesterday". Returns None otherwise."""
    match = _DUE_DATE_LINE.search(raw_text)
    if match is None:
        return None
    phrase = match.group(1).strip()
    if phrase.lower() in _UNRESOLVABLE_DUE_DATE_WORDS:
        return phrase
    return None


_PO_REFERENCE = re.compile(r"(?i)\bref\.?\s*(PO-\d+)\b|\b(PO-\d+)\b")


def find_purchase_order_reference(raw_text: str) -> str | None:
    """A purchase-order reference cited in free text, e.g. "Ref PO-20260115".

    Captured so its absence from any purchase-order record is statable (ticket 07)
    rather than silently unmentioned. Not matched against anything here.
    """
    match = _PO_REFERENCE.search(raw_text)
    if match is None:
        return None
    return match.group(1) or match.group(2)


__all__ = [
    "RepairResult",
    "repair_ocr_digits",
    "find_unresolvable_due_date",
    "find_purchase_order_reference",
]
