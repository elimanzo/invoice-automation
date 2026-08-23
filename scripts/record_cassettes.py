"""Record cassettes: `python scripts/record_cassettes.py`.

Runs the whole golden set (`data/invoices/` plus `data/invoices_extra/`) through the
real pipeline against the real Grok provider, and writes every `structured()`
request/response pair to `tests/cassettes/` — the same format `FakeProvider` already
reads (see `RecordingProvider` in `providers.py`), so the fake provider and cassette
replay are one implementation, not two.

Requires `XAI_API_KEY` (per ADR-0006, this is the one documented step that needs a
key). Costs real API calls: run it deliberately, not as part of any test or CI job,
and only after a prompt or schema change that makes the existing cassettes stale (see
`scripts/check_cassettes_fresh.py`).
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from invoice_automation.catalogue import SqliteCatalogue, seed_catalogue  # noqa: E402
from invoice_automation.clock import FixedClock  # noqa: E402
from invoice_automation.config import Settings  # noqa: E402
from invoice_automation.deps import Deps  # noqa: E402
from invoice_automation.evals import run_eval  # noqa: E402
from invoice_automation.payments import MockPayment  # noqa: E402
from invoice_automation.providers import GrokProvider, RecordingProvider  # noqa: E402
from invoice_automation.registry import SqliteRegistry  # noqa: E402

CASSETTES_DIR = REPO_ROOT / "tests" / "cassettes"
MANIFEST_PATH = CASSETTES_DIR / "_manifest.json"


def main() -> int:
    settings = Settings.from_env()
    if not settings.has_api_key:
        print(
            "XAI_API_KEY is not set. Recording cassettes needs a real key — "
            "copy .env.example to .env, fill it in, and export it into this shell.",
            file=sys.stderr,
        )
        return 1

    recorder = RecordingProvider(
        inner=GrokProvider(
            api_key=settings.api_key, base_url=settings.base_url, model=settings.model
        )
    )

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        catalogue_path = tmp_path / "catalogue.db"
        seed_catalogue(catalogue_path)
        deps = Deps(
            provider=recorder,
            catalogue=SqliteCatalogue(catalogue_path),
            payment=MockPayment(),
            clock=FixedClock(date(2026, 2, 1)),
            registry=SqliteRegistry(tmp_path / "registry.db"),
            model=settings.model,
        )
        report = run_eval(deps)

    print(report.summary())

    CASSETTES_DIR.mkdir(parents=True, exist_ok=True)
    for key, response in recorder.responses.items():
        (CASSETTES_DIR / f"{key}.json").write_text(
            json.dumps(response, indent=2, default=str) + "\n", encoding="utf-8"
        )
    MANIFEST_PATH.write_text(
        json.dumps(dict(sorted(recorder.fingerprints.items())), indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(recorder.responses)} cassette(s) to {CASSETTES_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
