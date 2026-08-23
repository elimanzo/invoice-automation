"""Settings, read from the environment.

Every value the system reads from outside itself is listed here and mirrored in
`.env.example`. Policy dials — thresholds, flag weights, FX rates — arrive with the
tickets that introduce them, and they live here rather than in the logic they govern, so
the policy is visible without reading the code that applies it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Named here rather than inline so `.env.example` and this module cannot drift.
ENV_API_KEY = "XAI_API_KEY"
ENV_BASE_URL = "XAI_BASE_URL"
ENV_MODEL = "INVOICE_MODEL"
ENV_DATA_DIR = "INVOICE_DATA_DIR"

DEFAULT_BASE_URL = "https://api.x.ai/v1"
DEFAULT_MODEL = "grok-4"


@dataclass(frozen=True)
class Settings:
    """Configuration for one run."""

    api_key: str | None = None
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    data_dir: str = ".data"
    catalogue_filename: str = "catalogue.db"
    registry_filename: str = "registry.db"

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            api_key=os.environ.get(ENV_API_KEY) or None,
            base_url=os.environ.get(ENV_BASE_URL, DEFAULT_BASE_URL),
            model=os.environ.get(ENV_MODEL, DEFAULT_MODEL),
            data_dir=os.environ.get(ENV_DATA_DIR, ".data"),
        )

    @property
    def has_api_key(self) -> bool:
        """Whether a real provider is available.

        Determines the default provider: the real one when a key is present, the fake
        one otherwise, so the system runs either way (ADR-0001).
        """
        return bool(self.api_key)
