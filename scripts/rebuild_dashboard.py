"""Rebuild the committed dashboard bundle: `python scripts/rebuild_dashboard.py`.

Runs the frontend's own build (`npm install && npm run build`, type-checking first),
then stamps the result with a hash of the sources that produced it. The stamp is what
`scripts/check_bundle_fresh.py` compares against later, to catch a bundle that was
edited by hand, or a source change that was never rebuilt — a static bundle
(ADR-0008) is only safe to commit if there is a way to tell it went stale.

Requires Node (`npm`) on PATH; the built system itself never does (ADR-0008).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = REPO_ROOT / "frontend"
STATIC_DIR = REPO_ROOT / "src" / "invoice_automation" / "static"

sys.path.insert(0, str(REPO_ROOT))
from scripts._bundle_hash import STAMP_FILENAME, compute_source_hash  # noqa: E402


def main() -> int:
    for command in (["npm", "install"], ["npm", "run", "build"]):
        result = subprocess.run(command, cwd=FRONTEND_DIR, shell=(sys.platform == "win32"))
        if result.returncode != 0:
            return result.returncode

    stamp = compute_source_hash(FRONTEND_DIR)
    (STATIC_DIR / STAMP_FILENAME).write_text(stamp + "\n", encoding="utf-8")
    print(f"Bundle rebuilt and stamped ({stamp[:12]}...).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
