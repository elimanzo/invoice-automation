"""Generates the one authored fixture that can't be hand-written as text: an
image-only PDF with no text layer.

Requires: Pillow (already a project dependency via pdfplumber's own image handling).

Usage: python data/generate_extra_fixtures.py
"""

import os

from PIL import Image

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "invoices_extra")


def create_image_only_pdf() -> None:
    """A page with pixels and nothing else — no text operators at all, so
    pdfplumber's `extract_text()` returns an empty string, exactly like a scanned
    invoice nobody ran OCR on."""
    path = os.path.join(OUTPUT_DIR, "invoice_9004_image_only.pdf")
    image = Image.new("RGB", (595, 842), color=(240, 240, 240))
    image.save(path, "PDF")
    print(f"  Created {os.path.basename(path)}")


if __name__ == "__main__":
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    create_image_only_pdf()
