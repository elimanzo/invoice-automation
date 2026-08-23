"""The catalogue: Acme's inventory and vendor master.

This is the ground truth validation checks against. The brief supplies a minimal seed of
items and stock levels; two columns are added beyond it, on the brief's own invitation to
"extend the seed data with additional items or columns" and to "consider adding tables"
for vendor information:

- **expected unit price** — what Acme normally pays, so an invoiced price has something
  to be compared against.
- **vendor master** — payees Acme recognises, so an unfamiliar payee is visible.

`Catalogue` is a protocol, so this SQLite implementation is one adapter among possible
others. Swapping in a real ERP is a construction change at the edge.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher
from pathlib import Path
from typing import Protocol, runtime_checkable

# Stock levels are the brief's. Prices are inferred from what the sample invoices
# routinely bill, which is what makes an outlier detectable.
SEED_ITEMS: tuple[tuple[str, int, str | None], ...] = (
    ("WidgetA", 15, "250.00"),
    ("WidgetB", 10, "500.00"),
    ("GadgetX", 5, "750.00"),
    # Present at zero stock per the brief. Acme has no price for it, because Acme does
    # not buy it — so there is nothing an invoiced price could be compared against.
    ("FakeItem", 0, None),
)

# Payees Acme recognises. Two vendors appearing in the sample data are deliberately
# absent: neither has a catalogue relationship, and an unfamiliar payee is exactly the
# signal a vendor master exists to raise. Absence produces a soft flag, never a
# rejection on its own — this list was authored, and the severity reflects that.
SEED_VENDORS: tuple[str, ...] = (
    "Widgets Inc.",
    "Gadgets Co.",
    "Precision Parts Ltd.",
    "Global Supply Chain Partners",
    "Acme Industrial Supplies",
    "MegaWidgets Corp",
    "Consolidated Materials Group",
    "Summit Manufacturing Co.",
    "Atlas Industrial Supply",
    "TechParts International",
    "Reliable Components Inc.",
    "QuickShip Distributers",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS inventory (
    item                TEXT PRIMARY KEY,
    stock               INTEGER NOT NULL,
    expected_unit_price TEXT
);
CREATE TABLE IF NOT EXISTS vendors (
    name TEXT PRIMARY KEY
);
"""


class UnknownItem(KeyError):
    """An operation named an item the catalogue does not hold."""


@dataclass(frozen=True)
class CatalogueItem:
    """An item Acme stocks."""

    name: str
    stock: int
    expected_unit_price: Decimal | None


@runtime_checkable
class Catalogue(Protocol):
    """Read access to inventory and the vendor master."""

    def get_item(self, name: str) -> CatalogueItem | None:
        """The catalogue entry for an exact item name, or None if Acme does not stock it."""
        ...

    def all_items(self) -> list[CatalogueItem]:
        """Every catalogue entry."""
        ...

    def is_known_vendor(self, name: str) -> bool:
        """Whether this payee appears in the vendor master."""
        ...


def seed_catalogue(path: Path, *, reset: bool = False) -> None:
    """Create the catalogue and populate it if it is empty.

    Seeding an already-populated catalogue does nothing, so a row deliberately deleted
    from a live catalogue stays deleted rather than reappearing on the next run.
    `reset=True` discards current contents and restores the seed, which is what the
    explicit command is for.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript(_SCHEMA)
        if reset:
            conn.execute("DELETE FROM inventory")
            conn.execute("DELETE FROM vendors")
        if conn.execute("SELECT 1 FROM inventory LIMIT 1").fetchone() is None:
            conn.executemany(
                "INSERT INTO inventory (item, stock, expected_unit_price) VALUES (?, ?, ?)",
                SEED_ITEMS,
            )
        if conn.execute("SELECT 1 FROM vendors LIMIT 1").fetchone() is None:
            conn.executemany(
                "INSERT INTO vendors (name) VALUES (?)",
                ((name,) for name in SEED_VENDORS),
            )


class SqliteCatalogue:
    """SQLite-backed catalogue.

    Self-seeds on construction when empty, so there is no setup step a reader can skip.
    The explicit seed command still exists for resetting deliberately.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        seed_catalogue(path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_item(self, name: str) -> CatalogueItem | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT item, stock, expected_unit_price FROM inventory WHERE item = ?",
                (name,),
            ).fetchone()
        return _to_item(row) if row else None

    def all_items(self) -> list[CatalogueItem]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT item, stock, expected_unit_price FROM inventory ORDER BY item"
            ).fetchall()
        return [_to_item(row) for row in rows]

    def is_known_vendor(self, name: str) -> bool:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT 1 FROM vendors WHERE name = ?", (name,)).fetchone()
        return row is not None

    def set_stock(self, item: str, stock: int) -> None:
        """Adjust stock. Not part of the read protocol; used by tests and tooling.

        Raises `UnknownItem` rather than silently affecting no rows, so a mistyped item
        name fails where it is made instead of somewhere later.
        """
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                "UPDATE inventory SET stock = ? WHERE item = ?", (stock, item)
            )
            if cursor.rowcount == 0:
                raise UnknownItem(f"{item!r} is not in the catalogue")


def _to_item(row: sqlite3.Row) -> CatalogueItem:
    price = row["expected_unit_price"]
    return CatalogueItem(
        name=row["item"],
        stock=row["stock"],
        expected_unit_price=Decimal(price) if price is not None else None,
    )


# ---------------------------------------------------------------------------
# Item matching (ADR-0007): normalise, then match exactly. The single source of truth
# for what an item name resolves to — both validation.py and the approval agent's
# read-only tools (tools.py) call these rather than each rolling their own matching, so
# they can never disagree about what's in the catalogue.
#
# Found live: before this existed, tools.py called `catalogue.get_item()` directly (an
# exact match), while validation.py normalised first. The approval agent asked about
# "Widget A" (as written in a real corrupted invoice) and was told it doesn't exist,
# while validation correctly matched it to WidgetA — the agent then escalated an invoice
# on a factually wrong premise its own tool had handed it.
# ---------------------------------------------------------------------------


def normalize_item_name(name: str) -> str:
    return "".join(c for c in name.lower() if c.isalnum())


def match_item(catalogue: Catalogue, name: str) -> CatalogueItem | None:
    """The catalogue entry matching `name` after normalisation, or None if nothing
    matches. Never a fuzzy match — see `nearest_item_name` for that, which only ever
    names a suggestion and is never used to decide whether something matched."""
    normalized = normalize_item_name(name)
    for item in catalogue.all_items():
        if normalize_item_name(item.name) == normalized:
            return item
    return None


def nearest_item_name(catalogue: Catalogue, name: str, floor: float = 0.6) -> str | None:
    """The closest catalogue entry name by similarity, for a flag or a hint only —
    never used to decide a match. Below `floor`, returns None rather than naming an
    unrelated item as though it were a near miss."""
    items = catalogue.all_items()
    if not items:
        return None
    normalized = normalize_item_name(name)
    best_name, best_ratio = None, 0.0
    for item in items:
        ratio = SequenceMatcher(None, normalized, normalize_item_name(item.name)).ratio()
        if ratio > best_ratio:
            best_name, best_ratio = item.name, ratio
    return best_name if best_ratio >= floor else None
