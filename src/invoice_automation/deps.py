"""The dependency container.

Everything non-deterministic — the reasoning engine, the catalogue, the payment gateway,
the clock, the registry — enters the system through this one object. That is what makes
the pipeline testable without patching anything: a test constructs `Deps` with fakes and
the pipeline never knows.

One container rather than five parameters, so that adding a sixth dependency later
touches one type instead of every call site and every test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .cache import LLMCache, NullCache, SqliteCache
from .catalogue import Catalogue, SqliteCatalogue
from .clock import Clock, SystemClock
from .config import DEFAULT_MODEL, MissingApiKey, Settings
from .overrides import InMemoryOverrideStore, OverrideStore, SqliteOverrideStore
from .payments import MockPayment, PaymentGateway
from .providers import FakeProvider, GrokProvider, Provider
from .registry import Registry, SqliteRegistry
from .tracing import InMemoryTracer, SqliteTracer, Tracer


@dataclass(frozen=True)
class Deps:
    """Every dependency the pipeline needs from the outside world."""

    provider: Provider
    catalogue: Catalogue
    payment: PaymentGateway
    clock: Clock
    registry: Registry
    model: str = DEFAULT_MODEL
    """Which model `provider` calls — carried here rather than read off the provider
    itself, since `FakeProvider` doesn't name one. Only used for cost accounting
    (ticket 12); it names nothing the pipeline behaves differently for."""
    provider_name: str = "fake"
    """"grok" or "fake" — same reasoning as `model`: only for display (the dashboard's
    header badge), never branched on by the pipeline itself."""
    tracer: Tracer = field(default_factory=InMemoryTracer)
    cache: LLMCache = field(default_factory=NullCache)
    overrides: OverrideStore = field(default_factory=InMemoryOverrideStore)


def build_deps(settings: Settings | None = None, *, provider: str | None = None) -> Deps:
    """Construct the production dependency set.

    This function is the edge. Swapping the SQLite catalogue for a real ERP, or the mock
    payment gateway for a banking API, is a change here and nowhere else — no pipeline
    stage names an implementation.

    `provider` forces a choice ("grok" or "fake"); omitted, Grok is used when a key is
    configured and the fake otherwise, so the system runs either way (ADR-0001).
    """
    settings = settings or Settings.from_env()
    data_dir = Path(settings.data_dir)
    chosen = provider or ("grok" if settings.has_api_key else "fake")

    cache: LLMCache = (
        SqliteCache(data_dir / "llm_cache.db") if settings.cache_enabled else NullCache()
    )

    return Deps(
        provider=_build_provider(settings, chosen),
        catalogue=SqliteCatalogue(data_dir / settings.catalogue_filename),
        payment=MockPayment(),
        clock=SystemClock(),
        registry=SqliteRegistry(data_dir / settings.registry_filename),
        model=settings.model,
        provider_name=chosen,
        tracer=SqliteTracer(data_dir / "trace.db"),
        cache=cache,
        overrides=SqliteOverrideStore(data_dir / "overrides.db"),
    )


def _build_provider(settings: Settings, provider: str | None) -> Provider:
    chosen = provider or ("grok" if settings.has_api_key else "fake")
    if chosen == "grok":
        if not settings.has_api_key:
            from .config import ENV_API_KEY

            raise MissingApiKey(
                f"--provider grok requires {ENV_API_KEY} to be set. "
                "Copy .env.example to .env and fill it in, or omit --provider to use "
                "the fake provider instead."
            )
        assert settings.api_key is not None  # has_api_key just confirmed it
        return GrokProvider(
            api_key=settings.api_key, base_url=settings.base_url, model=settings.model
        )
    return FakeProvider.with_sample_responses()
