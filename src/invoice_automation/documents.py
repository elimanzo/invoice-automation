"""Loading a document from disk.

A **document** is one file carrying an invoice. This module answers what format it is and
hands back its raw content; deciding what the content *means* is extraction's job.

Format-specific parsing arrives in tickets 04 and 05 — including the decision that
structured formats are parsed by code rather than by a model (ADR-0009). For now every
document is read as text, which is all the tracer bullet needs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .models import DocumentFormat

_SUFFIX_FORMATS = {
    ".txt": DocumentFormat.TEXT,
    ".json": DocumentFormat.JSON,
    ".csv": DocumentFormat.CSV,
    ".xml": DocumentFormat.XML,
    ".pdf": DocumentFormat.PDF,
}


# Tried in order. UTF-8 first because it is correct for everything the sample data
# contains; cp1252 second because ERP exports commonly use it and its bytes are
# indistinguishable from latin-1 for the punctuation that actually appears in invoices.
_ENCODINGS = ("utf-8", "cp1252")


class UnsupportedDocument(Exception):
    """The file is not a format this system reads."""


class UndecodableDocument(Exception):
    """The file could not be decoded as text in any supported encoding."""


@dataclass(frozen=True)
class Document:
    """One file carrying an invoice."""

    path: Path
    format: DocumentFormat
    raw_text: str

    @property
    def name(self) -> str:
        return self.path.name


def load_document(path: Path) -> Document:
    """Read a document from disk.

    Format comes from the suffix for now; content sniffing arrives with ticket 04, where
    it matters because a mislabelled file should still parse.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"No such document: {path}")

    document_format = _SUFFIX_FORMATS.get(path.suffix.lower())
    if document_format is None:
        raise UnsupportedDocument(
            f"{path.name}: unsupported format {path.suffix!r}. "
            f"Supported: {', '.join(sorted(_SUFFIX_FORMATS))}"
        )
    if document_format is DocumentFormat.PDF:
        raise UnsupportedDocument(
            f"{path.name}: PDF extraction arrives with ticket 04. "
            "The text twin of this document can be used meanwhile."
        )

    return Document(path=path, format=document_format, raw_text=_read_text(path))


def _read_text(path: Path) -> str:
    """Decode a document, refusing to guess.

    Substituting replacement characters would corrupt vendor and item names in the one
    module whose whole contract is fidelity to the document — and extraction is
    explicitly instructed to preserve names exactly. A file we cannot decode is a file
    we must not pretend to have read.
    """
    raw = path.read_bytes()
    for encoding in _ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UndecodableDocument(
        f"{path.name}: not decodable as {' or '.join(_ENCODINGS)}. "
        "Convert the file to UTF-8 and retry."
    )
