"""The catalogue: Acme's inventory ground truth."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from invoice_automation.catalogue import SqliteCatalogue, seed_catalogue


def test_seed_covers_the_items_the_brief_specifies(tmp_path: Path) -> None:
    catalogue = SqliteCatalogue(tmp_path / "catalogue.db")

    assert {item.name: item.stock for item in catalogue.all_items()} == {
        "WidgetA": 15,
        "WidgetB": 10,
        "GadgetX": 5,
        "FakeItem": 0,
    }


def test_items_carry_an_expected_unit_price(tmp_path: Path) -> None:
    catalogue = SqliteCatalogue(tmp_path / "catalogue.db")

    widget_a = catalogue.get_item("WidgetA")
    gadget_x = catalogue.get_item("GadgetX")
    assert widget_a is not None and gadget_x is not None
    assert widget_a.expected_unit_price == Decimal("250.00")
    assert gadget_x.expected_unit_price == Decimal("750.00")


def test_an_item_acme_does_not_stock_has_no_price(tmp_path: Path) -> None:
    catalogue = SqliteCatalogue(tmp_path / "catalogue.db")

    # FakeItem exists at zero stock per the brief, but Acme has no price for it:
    # there is nothing to compare an invoiced price against.
    fake_item = catalogue.get_item("FakeItem")
    assert fake_item is not None
    assert fake_item.expected_unit_price is None


def test_an_uncatalogued_item_is_absent_not_invented(tmp_path: Path) -> None:
    catalogue = SqliteCatalogue(tmp_path / "catalogue.db")

    assert catalogue.get_item("WidgetC") is None
    assert catalogue.get_item("SuperGizmo") is None


def test_vendor_master_distinguishes_known_from_unknown(tmp_path: Path) -> None:
    catalogue = SqliteCatalogue(tmp_path / "catalogue.db")

    assert catalogue.is_known_vendor("Widgets Inc.") is True
    assert catalogue.is_known_vendor("Fraudster LLC") is False
    assert catalogue.is_known_vendor("NoProd Industries") is False


def test_catalogue_self_seeds_on_first_use(tmp_path: Path) -> None:
    """No separate seed step can be skipped, because there isn't one to skip."""
    path = tmp_path / "nested" / "catalogue.db"
    assert not path.exists()

    catalogue = SqliteCatalogue(path)

    assert catalogue.get_item("WidgetA") is not None
    assert path.exists()


def test_explicit_seed_resets_a_modified_catalogue(tmp_path: Path) -> None:
    path = tmp_path / "catalogue.db"
    catalogue = SqliteCatalogue(path)
    catalogue.set_stock("WidgetA", 0)
    emptied = catalogue.get_item("WidgetA")
    assert emptied is not None and emptied.stock == 0

    seed_catalogue(path, reset=True)

    restored = SqliteCatalogue(path).get_item("WidgetA")
    assert restored is not None and restored.stock == 15
