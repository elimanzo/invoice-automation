"""Regressions for failure modes found in review of ticket 01.

Each of these was a way for the system to be quietly wrong rather than loudly broken,
which is the class of bug this project exists to prevent.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from invoice_automation.catalogue import SqliteCatalogue, UnknownItem, seed_catalogue
from invoice_automation.config import DEFAULT_DATA_DIR, DEFAULT_MODEL, Settings
from invoice_automation.deps import Deps
from invoice_automation.documents import UndecodableDocument, load_document
from invoice_automation.extraction import extract_invoice
from invoice_automation.models import Invoice, LineItem, Vendor
from invoice_automation.providers import FakeProvider, MissingRecording, StructuredCall


class TestSettings:
    def test_blank_env_var_falls_back_to_the_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A `.env` file conventionally carries blank keys; blank must mean "unset".

        Otherwise a blank data dir resolves to Path("") and the databases land in the
        working directory.
        """
        monkeypatch.setenv("INVOICE_DATA_DIR", "")
        monkeypatch.setenv("INVOICE_MODEL", "   ")

        settings = Settings.from_env()

        assert settings.data_dir == DEFAULT_DATA_DIR
        assert settings.model == DEFAULT_MODEL

    def test_blank_api_key_is_absent_not_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XAI_API_KEY", "")

        assert Settings.from_env().api_key is None
        assert Settings.from_env().has_api_key is False


class TestFakeProvider:
    def test_an_invoice_and_its_revision_do_not_collide(self, deps: Deps) -> None:
        """Two documents state INV-1004. Keying on invoice number would confuse them."""
        first = StructuredCall(system="", user="", schema={}, document_id="invoice_1004.json")
        second = StructuredCall(
            system="", user="", schema={}, document_id="invoice_1004_revised.json"
        )

        assert Path(first.document_id).stem != Path(second.document_id).stem

    def test_a_missing_recording_raises_rather_than_inventing(self, deps: Deps) -> None:
        call = StructuredCall(system="", user="", schema={}, document_id="invoice_9999.txt")

        with pytest.raises(MissingRecording) as excinfo:
            deps.provider.structured(call)

        assert "invoice_9999.txt" in str(excinfo.value)

    def test_an_empty_response_set_fails_at_construction(self, tmp_path: Path) -> None:
        """A build that omits package data must fail loudly, not answer nothing."""
        with pytest.raises(FileNotFoundError):
            FakeProvider.with_sample_responses(tmp_path / "does-not-exist")

        (tmp_path / "empty").mkdir()
        with pytest.raises(FileNotFoundError):
            FakeProvider.with_sample_responses(tmp_path / "empty")

    def test_a_caller_cannot_corrupt_the_store(self, tmp_path: Path) -> None:
        directory = tmp_path / "responses"
        directory.mkdir()
        (directory / "doc.json").write_text(json.dumps({"vendor": {"name": "A"}}), "utf-8")
        provider = FakeProvider.with_sample_responses(directory)
        call = StructuredCall(system="", user="", schema={}, document_id="doc.json")

        first = provider.structured(call)
        first["vendor"]["name"] = "mutated"

        assert provider.structured(call)["vendor"]["name"] == "A"


class TestInvoiceTotals:
    def test_an_unknown_line_amount_does_not_become_zero(self) -> None:
        """Zeroing an unknown line makes the sum smaller, so a short invoice could
        match its stated total and pass a check it should have failed."""
        invoice = Invoice(
            vendor=Vendor(name="Widgets Inc."),
            line_items=[
                LineItem(item="WidgetA", quantity=2, unit_price=Decimal("250.00")),
                LineItem(item="WidgetB", quantity=1),  # no price stated anywhere
            ],
            total=Decimal("500.00"),
        )

        assert invoice.unpriced_line_count == 1
        assert invoice.line_items_total is None

    def test_a_complete_invoice_still_totals(self) -> None:
        invoice = Invoice(
            vendor=Vendor(name="Widgets Inc."),
            line_items=[LineItem(item="WidgetA", quantity=2, unit_price=Decimal("250.00"))],
        )

        assert invoice.unpriced_line_count == 0
        assert invoice.line_items_total == Decimal("500.00")


class TestCatalogue:
    def test_seeding_does_not_resurrect_a_deleted_item(self, tmp_path: Path) -> None:
        """Acme dropping an item from its catalogue must stay dropped."""
        import sqlite3

        path = tmp_path / "catalogue.db"
        SqliteCatalogue(path)
        with sqlite3.connect(path) as conn:
            conn.execute("DELETE FROM inventory WHERE item = 'FakeItem'")

        assert SqliteCatalogue(path).get_item("FakeItem") is None

    def test_explicit_reset_does_restore_the_seed(self, tmp_path: Path) -> None:
        path = tmp_path / "catalogue.db"
        catalogue = SqliteCatalogue(path)
        catalogue.set_stock("WidgetA", 0)

        seed_catalogue(path, reset=True)

        restored = SqliteCatalogue(path).get_item("WidgetA")
        assert restored is not None and restored.stock == 15

    def test_setting_stock_on_an_unknown_item_raises(self, tmp_path: Path) -> None:
        catalogue = SqliteCatalogue(tmp_path / "catalogue.db")

        with pytest.raises(UnknownItem):
            catalogue.set_stock("WidgetTypo", 5)

    def test_the_database_file_is_not_held_open(self, tmp_path: Path) -> None:
        """An unclosed handle blocks deletion on Windows and leaks per lookup."""
        path = tmp_path / "catalogue.db"
        catalogue = SqliteCatalogue(path)
        for _ in range(20):
            catalogue.get_item("WidgetA")
            catalogue.is_known_vendor("Widgets Inc.")

        path.unlink()  # raises PermissionError on Windows if a handle is still open
        assert not path.exists()


class TestDocumentDecoding:
    def test_an_undecodable_document_is_refused_not_mangled(
        self, tmp_path: Path, deps: Deps
    ) -> None:
        """Replacement characters in a vendor name would be reported as fidelity."""
        path = tmp_path / "invoice_bad.txt"
        path.write_bytes(b"Vendor: \xff\xfe\xfd invalid \x81\x8d\x8f")

        with pytest.raises(UndecodableDocument):
            load_document(path)

    def test_a_cp1252_document_is_read_not_refused(self, tmp_path: Path) -> None:
        """ERP exports are commonly cp1252; that is decodable, so decode it."""
        path = tmp_path / "invoice_1001.txt"
        path.write_bytes("Vendor: Café Widgets — Ltd.".encode("cp1252"))

        assert "Café Widgets" in load_document(path).raw_text


def test_extraction_identifies_the_document_not_the_invoice_number(
    invoices_dir: Path, deps: Deps
) -> None:
    """The provider is told which document it is reading, explicitly."""
    document = load_document(invoices_dir / "invoice_1001.txt")

    extract_invoice(document, deps)

    assert isinstance(deps.provider, FakeProvider)
    assert deps.provider.calls[-1].document_id == "invoice_1001.txt"
