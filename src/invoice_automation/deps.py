"""The dependency container.

Everything non-deterministic — the reasoning engine, the catalogue, the payment gateway,
the clock, the registry — enters the system through this one object. That is what makes
the pipeline testable without patching anything: a test constructs `Deps` with fakes and
the pipeline never knows.

One container rather than five parameters, so that adding a sixth dependency later
touches one type instead of every call site and every test.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .catalogue import Catalogue, SqliteCatalogue
from .clock import Clock, SystemClock
from .config import Settings
from .payments import MockPayment, PaymentGateway
from .providers import FakeProvider, Provider
from .registry import Registry, SqliteRegistry


@dataclass(frozen=True)
class Deps:
    """Every dependency the pipeline needs from the outside world."""

    provider: Provider
    catalogue: Catalogue
    payment: PaymentGateway
    clock: Clock
    registry: Registry


def build_deps(settings: Settings | None = None) -> Deps:
    """Construct the production dependency set.

    This function is the edge. Swapping the SQLite catalogue for a real ERP, or the mock
    payment gateway for a banking API, is a change here and nowhere else — no pipeline
    stage names an implementation.

    The Grok provider arrives with ticket 03; until then the fake is the only one.
    """
    settings = settings or Settings.from_env()
    data_dir = Path(settings.data_dir)

    return Deps(
        provider=FakeProvider.with_sample_responses(),
        catalogue=SqliteCatalogue(data_dir / settings.catalogue_filename),
        payment=MockPayment(),
        clock=SystemClock(),
        registry=SqliteRegistry(data_dir / settings.registry_filename),
    )
