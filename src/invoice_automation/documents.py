"""Loading a document from disk.

A **document** is one file carrying an invoice. This module answers what format it is and
hands back its raw text content; deciding what the content *means* is extraction's job.
Deterministic parsing of structured formats (ADR-0009) is a separate module (ticket 05) —
here, every format is reduced to text, the same as it always was for `.txt`.

This is the only module that imports the PDF library, and the only one that needs to.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

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


class UnreadableDocument(Exception):
    """The format is supported, but no text could be extracted from this file.

    An image-only PDF is the case this exists for: the format is fine, the bytes parse,
    there is simply no text layer to read. Failing loudly here is the point — a document
    silently reduced to an empty string would sail through extraction and validation as
    if it were a legitimately blank invoice.
    """


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

    Format is sniffed from content first, falling back to the file suffix — a JSON
    invoice saved with the wrong extension still parses as JSON. CSV and plain text have
    no reliable content signature, so those two are suffix-only.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"No such document: {path}")

    raw_bytes = path.read_bytes()
    document_format = _sniff_format(raw_bytes) or _SUFFIX_FORMATS.get(path.suffix.lower())
    if document_format is None:
        raise UnsupportedDocument(
            f"{path.name}: unsupported format {path.suffix!r}. "
            f"Supported: {', '.join(sorted(_SUFFIX_FORMATS))}"
        )

    if document_format is DocumentFormat.PDF:
        raw_text = _extract_pdf_text(path)
    else:
        raw_text = _decode_text(path.name, raw_bytes)

    return Document(path=path, format=document_format, raw_text=raw_text)


def _sniff_format(raw_bytes: bytes) -> DocumentFormat | None:
    """Detect format from the bytes themselves, where the format has a real signature.

    CSV and plain text have none — a CSV's first bytes look like any other text, and
    forcing a signature there would misdetect more than it fixes. Those two fall through
    to the suffix.

    JSON and XML are confirmed by actually parsing, not just a leading brace or angle
    bracket: a forwarded-email body that happens to open with a bracketed token (a
    subject tag, an HTML fragment) would otherwise be misdetected and extracted under
    the wrong format's prompt. A leading character is a hint about which parser to try,
    never the verdict on its own.
    """
    stripped = raw_bytes.lstrip()
    if stripped.startswith(b"%PDF-"):
        return DocumentFormat.PDF

    if stripped[:1] in (b"{", b"["):
        try:
            json.loads(raw_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        return DocumentFormat.JSON

    if stripped[:1] == b"<":
        try:
            ElementTree.fromstring(raw_bytes)
        except ElementTree.ParseError:
            return None
        return DocumentFormat.XML

    return None


def _decode_text(name: str, raw_bytes: bytes) -> str:
    """Decode a document, refusing to guess.

    Substituting replacement characters would corrupt vendor and item names in the one
    module whose whole contract is fidelity to the document — and extraction is
    explicitly instructed to preserve names exactly. A file we cannot decode is a file
    we must not pretend to have read.
    """
    for encoding in _ENCODINGS:
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UndecodableDocument(
        f"{name}: not decodable as {' or '.join(_ENCODINGS)}. Convert the file to UTF-8 "
        "and retry."
    )


def _extract_pdf_text(path: Path) -> str:
    """Extract text from every page of a PDF, refusing to return an empty invoice.

    Catches broadly around pdfplumber/pdfminer: a corrupt or encrypted PDF can raise
    from deep inside that library's own exception hierarchy, which is not part of this
    codebase's public contract to enumerate. At this boundary — an arbitrary file handed
    in from outside the system — any failure to read text out of it means the same thing
    to every caller: the document could not be read, name it and say so.
    """
    try:
        with pdfplumber.open(path) as pdf:
            pages_text = [page.extract_text() or "" for page in pdf.pages]
    except Exception as exc:
        raise UnreadableDocument(f"{path.name}: could not read the PDF: {exc}") from exc

    combined = "\n".join(pages_text).strip()
    if not combined:
        raise UnreadableDocument(
            f"{path.name}: no extractable text — this looks like an image-only PDF with "
            "no text layer. OCR or a vision-based extraction path would be needed to "
            "read it; neither is in scope yet."
        )
    return combined
