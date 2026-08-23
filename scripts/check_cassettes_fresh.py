"""Detect cassettes that no longer match the prompts/schemas the code currently
builds: `python scripts/check_cassettes_fresh.py`.

Runs the golden set through the cassette-backed fake provider (no network, no key) and
compares the fingerprint of every request the pipeline actually built against the
fingerprint recorded alongside each cassette by `scripts/record_cassettes.py`. A
prompt or schema edit changes the fingerprint without changing the recorded response,
which is exactly the silent-staleness failure mode ADR-0006 calls out: the cassette
still "passes" because it still returns *some* well-formed answer, just not an answer
to the question the code now asks.

Exits non-zero and lists the stale documents if any fingerprint has drifted, or if a
cassette exists with no fingerprint recorded for it at all (an older recording run, or
a hand-edited file).
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
from invoice_automation.deps import Deps  # noqa: E402
from invoice_automation.evals import run_eval  # noqa: E402
from invoice_automation.payments import MockPayment  # noqa: E402
from invoice_automation.providers import FakeProvider, StructuredCall, request_fingerprint  # noqa: E402
from invoice_automation.registry import SqliteRegistry  # noqa: E402

CASSETTES_DIR = REPO_ROOT / "tests" / "cassettes"
MANIFEST_PATH = CASSETTES_DIR / "_manifest.json"


class _FingerprintingFakeProvider(FakeProvider):
    """`FakeProvider`, plus recording the fingerprint of every request it answered —
    the "current" side of the staleness comparison."""

    def __init__(self, responses: dict[str, dict[str, object]]) -> None:
        super().__init__(responses=responses)
        self.current_fingerprints: dict[str, str] = {}

    def structured(self, call: StructuredCall) -> dict[str, object]:
        key = Path(call.document_id).stem
        if call.kind == "critique":
            key = f"{key}.critique"
        self.current_fingerprints[key] = request_fingerprint(call)
        return super().structured(call)


def main() -> int:
    if not MANIFEST_PATH.is_file():
        print(
            f"No manifest at {MANIFEST_PATH}. Run scripts/record_cassettes.py first.",
            file=sys.stderr,
        )
        return 1
    recorded_fingerprints: dict[str, str] = json.loads(
        MANIFEST_PATH.read_text(encoding="utf-8")
    )

    provider = _FingerprintingFakeProvider(FakeProvider.with_sample_responses(CASSETTES_DIR).responses)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        catalogue_path = tmp_path / "catalogue.db"
        seed_catalogue(catalogue_path)
        deps = Deps(
            provider=provider,
            catalogue=SqliteCatalogue(catalogue_path),
            payment=MockPayment(),
            clock=FixedClock(date(2026, 2, 1)),
            registry=SqliteRegistry(tmp_path / "registry.db"),
        )
        run_eval(deps)

    stale = sorted(
        key
        for key, current in provider.current_fingerprints.items()
        if recorded_fingerprints.get(key) != current
    )
    if stale:
        print("Stale cassette(s) — prompt or schema changed since recording:", file=sys.stderr)
        for key in stale:
            reason = "no fingerprint on record" if key not in recorded_fingerprints else "prompt/schema changed"
            print(f"  {key}: {reason}", file=sys.stderr)
        print("\nRe-record with: python scripts/record_cassettes.py", file=sys.stderr)
        return 1

    print(f"{len(provider.current_fingerprints)} cassette(s) match the current prompts and schemas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
