"""Detect a committed dashboard bundle that is stale against its source:
`python scripts/check_bundle_fresh.py`.

The bundle under `src/invoice_automation/static/` is committed so the system runs
with no Node toolchain (ADR-0008) — which means nothing enforces that it was actually
rebuilt after the last frontend change, short of a human remembering. This compares
the hash `scripts/rebuild_dashboard.py` stamped at build time against a fresh hash of
the current frontend sources; a frontend edit with no matching rebuild fails this
loudly instead of silently shipping stale JS.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = REPO_ROOT / "frontend"
STATIC_DIR = REPO_ROOT / "src" / "invoice_automation" / "static"

sys.path.insert(0, str(REPO_ROOT))
from scripts._bundle_hash import STAMP_FILENAME, compute_source_hash  # noqa: E402


def main() -> int:
    stamp_path = STATIC_DIR / STAMP_FILENAME
    if not stamp_path.is_file():
        print(
            f"No bundle stamp at {stamp_path}. Run scripts/rebuild_dashboard.py.",
            file=sys.stderr,
        )
        return 1

    stamped = stamp_path.read_text(encoding="utf-8").strip()
    current = compute_source_hash(FRONTEND_DIR)
    if stamped != current:
        print(
            "The committed dashboard bundle is stale against frontend/src — "
            "run: python scripts/rebuild_dashboard.py",
            file=sys.stderr,
        )
        return 1

    print("Dashboard bundle matches its frontend source.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
