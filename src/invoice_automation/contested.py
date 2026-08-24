"""Contested submissions: two documents in one batch that both claim to be the current
version of the same invoice.

`reconciliation.py` handles a second document arriving *after* a first — a revision
supersedes what came before, and if the original was already paid, that is an exception a
human must resolve (`RevisionAfterPayment`). What that cannot do is decide which document
should have been first. A batch iterates a directory in sorted order, so
`invoice_1004.json` reaches approval before `invoice_1004_revised.json` exists as far as
the pipeline is concerned: the original auto-approves and pays $1,890, and only then does
the revision arrive to announce the real obligation is $5,940. Reversing the sort order
just moves the problem to a different pair. Filename order is not a business rule, and it
should not be what decides which invoice Acme pays.

So the collision is detected *before* any run starts, and both documents are held for a
human. Neither auto-pays; a reviewer says which version is current. This is deliberately
the cautious direction — a batch that escalates two documents a human would have waved
through costs a few minutes, and a batch that pays a superseded invoice costs the
difference between the two totals.

**What this can see.** Identity is read here without the model: structured documents
(JSON/CSV/XML) parse deterministically (ADR-0009), so their invoice number and `revision`
field are free to inspect. A `.txt` or `.pdf` document's identity is only known after
extraction, which means it cannot participate in a pre-scan that must finish before the
first run begins — see ADR-0013 for why paying an LLM per document twice was not worth
closing that gap for sample data where every revision is structured. A text-format
revision would still be caught by reconciliation, just one document later.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Iterable

from .documents import load_document
from .models import Flag, FlagSeverity
from .registry import normalize_invoice_identity
from .structured_parsing import STRUCTURED_FORMATS, parse_structured

CONTESTED_FLAG_CODE = "contested_submission"


def contested_documents(paths: Iterable[Path]) -> dict[str, str]:
    """Document name -> why it is contested, for every document in a collision group.

    A group is contested when two or more documents claim the same invoice identity and
    disagree about which of them is current — a revision alongside a non-revision, or two
    different revision labels. Two documents for the same identity making the same claim
    are *not* contested: that is an ordinary duplicate or enrichment, and reconciliation
    resolves it correctly whichever arrives first.

    Identity is `normalize_invoice_identity` — the digit run, vendor not considered, the
    same notion `registry` keys payments on. Two vendors independently numbering an
    invoice `INV-1004` therefore collide here, and the hold is the *conservative* end of
    that pre-existing ambiguity: payment idempotency already treats them as one
    obligation and would pay only the first, so a human seeing both beats a silent single
    payment. A vendor tiebreak (anticipated in `normalize_invoice_identity`'s own
    docstring) would have to land there, for the whole system, not here alone.
    """
    by_identity: dict[str, list[tuple[str, str | None]]] = defaultdict(list)

    for path in paths:
        peeked = _peek(path)
        if peeked is None:
            continue
        identity, revision = peeked
        by_identity[identity].append((path.name, revision))

    contested: dict[str, str] = {}
    for identity, members in by_identity.items():
        if len(members) < 2:
            continue
        # Two documents are only contested if they disagree about which is current.
        # Distinct version claims — a revision alongside a non-revision, or two different
        # revision labels — mean something has to choose between them. Two copies of the
        # *same* revision claim make the same claim twice: that is an ordinary duplicate,
        # reconciliation drops the redundant one, and holding them would block the
        # unambiguously current invoice from ever paying.
        claims = {revision for _, revision in members}
        if len(claims) < 2 or claims == {None}:
            continue

        names = sorted(name for name, _ in members)
        for name, revision in members:
            others = ", ".join(repr(other) for other in names if other != name)
            role = f"revision {revision!r}" if revision is not None else "no revision stated"
            contested[name] = (
                f"invoice {identity} arrived as {len(members)} documents in this batch "
                f"({role} here, alongside {others}), and they disagree about which is "
                "current. That is a human's call, not the file order's — neither version "
                "pays automatically."
            )

    return contested


def _peek(path: Path) -> tuple[str, str | None] | None:
    """This document's `(identity, revision)`, or None if that cannot be known without
    the model. Every failure is a None: a pre-scan is an optimisation over the safety
    reconciliation already provides, so it declines to answer rather than guessing, and
    never turns a malformed file into a batch-wide failure. The document itself still
    fails loudly a moment later when the batch actually processes it.
    """
    if not path.is_file():
        return None
    # A PDF can never be a structured format, and text-extracting one is the most
    # expensive load in the batch — skipped by suffix here rather than by format after
    # loading, so the pre-scan doesn't do pdfplumber's work twice for nothing. Every
    # other suffix still goes through `load_document`, whose byte-sniffing is what
    # catches a JSON payload in a `.txt` file.
    if path.suffix.lower() == ".pdf":
        return None
    try:
        document = load_document(path)
    except Exception:  # noqa: BLE001 — see the docstring
        return None

    if document.format not in STRUCTURED_FORMATS:
        return None

    try:
        invoice = parse_structured(document)
    except Exception:  # noqa: BLE001 — see the docstring
        return None

    identity = normalize_invoice_identity(invoice.invoice_number)
    if identity is None:
        return None
    return identity, invoice.revision


def contested_flag(reason: str) -> Flag:
    """The flag a held document carries. Soft, not fatal: the invoice is not wrong, and
    one of these two documents is almost certainly payable — a human just has to say
    which. Its weight (`config.RISK_WEIGHTS`) equals the escalation threshold, so it
    forces review on its own without pretending to be a rejection.
    """
    return Flag(severity=FlagSeverity.SOFT, code=CONTESTED_FLAG_CODE, message=reason)
