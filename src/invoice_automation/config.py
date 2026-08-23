"""Settings, read from the environment.

Every value the system reads from outside itself is listed here and mirrored in
`.env.example`. Policy dials — thresholds, flag weights, FX rates — arrive with the
tickets that introduce them, and they live here rather than in the logic they govern, so
the policy is visible without reading the code that applies it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal

# Named here rather than inline so `.env.example` and this module cannot drift.
ENV_API_KEY = "XAI_API_KEY"
ENV_BASE_URL = "XAI_BASE_URL"
ENV_MODEL = "INVOICE_MODEL"
ENV_DATA_DIR = "INVOICE_DATA_DIR"

DEFAULT_BASE_URL = "https://api.x.ai/v1"
DEFAULT_MODEL = "grok-4"
DEFAULT_DATA_DIR = ".data"

# Total attempts at extraction, including the first: 2 means one retry. A schema
# violation is fed back to the model as feedback before the retry (ticket 03's
# self-correction loop). A module constant rather than a Settings field: nothing
# threads Settings through to extraction.py, and a per-run override that no caller
# could actually set would be a knob that does nothing.
DEFAULT_EXTRACTION_MAX_ATTEMPTS = 2


class MissingApiKey(Exception):
    """A real provider was requested but no API key is configured."""


class UnknownCurrency(Exception):
    """An invoice states a currency this system has no conversion rate for."""


# USD per one unit of the currency, e.g. 1 EUR = 1.08 USD. Only currencies actually
# appearing in the sample data are listed; ticket 07's own ADR notes this changes no
# outcome there (INV-1014's EUR total converts to well under the scrutiny threshold
# either way) — it exists so the comparison is correct on principle, not by accident.
FX_RATES_TO_USD: dict[str, Decimal] = {
    "USD": Decimal("1.00"),
    "EUR": Decimal("1.08"),
}

# Crossed by compounding soft flags, not by any single one alone — this is what makes
# INV-1008 (unknown vendor, two uncatalogued items, $9,900 — just under the dollar
# threshold) escalate on judgement rather than sail through on a technicality.
RISK_ESCALATION_THRESHOLD = 5

# What a soft flag costs toward the risk score, by flag code. A flag with no entry here
# contributes nothing — fatal flags reject outright before risk is ever computed, and
# info flags are visibility only.
RISK_WEIGHTS: dict[str, int] = {
    "unknown_vendor": 3,
    "unknown_item": 3,
    "empty_vendor": 3,
    "price_above_expected": 3,
    "price_above_expected_documented": 1,
    "non_usd_currency": 1,
    "due_date_before_invoice_date": 2,
    "due_date_in_the_past": 2,
    # Two documents for the same invoice disagree, and reconciliation refuses to guess
    # which is right (reconciliation.py). This alone must force escalation — set equal
    # to the threshold itself so it always crosses it, referencing the constant rather
    # than a hardcoded number that could silently drift out of sync with it.
    "duplicate_contradiction": RISK_ESCALATION_THRESHOLD,
}

# The approval agent's investigation is bounded: this many tool calls before it must
# conclude. Exhausting the bound without a conclusion fails closed — the deterministic
# rule-based decision stands unchanged, never guessed at from an inconclusive
# investigation.
APPROVAL_MAX_TOOL_CALLS = 4


def _env(name: str, default: str) -> str:
    """Read an environment variable, treating empty as unset.

    A `.env` file conventionally carries keys with blank values, so `FOO=` must mean "use
    the default" rather than "use the empty string". Without this, a blank
    `INVOICE_DATA_DIR` would resolve to `Path("")` and write the databases into whatever
    the working directory happens to be.
    """
    return os.environ.get(name, "").strip() or default


@dataclass(frozen=True)
class Settings:
    """Configuration for one run."""

    api_key: str | None = None
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    data_dir: str = DEFAULT_DATA_DIR
    catalogue_filename: str = "catalogue.db"
    registry_filename: str = "registry.db"

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            api_key=os.environ.get(ENV_API_KEY, "").strip() or None,
            base_url=_env(ENV_BASE_URL, DEFAULT_BASE_URL),
            model=_env(ENV_MODEL, DEFAULT_MODEL),
            data_dir=_env(ENV_DATA_DIR, DEFAULT_DATA_DIR),
        )

    @property
    def has_api_key(self) -> bool:
        """Whether a real provider is available.

        Determines the default provider: the real one when a key is present, the fake
        one otherwise, so the system runs either way (ADR-0001).
        """
        return bool(self.api_key)
