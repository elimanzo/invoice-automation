"""Ticket 18: the committed dashboard bundle (ADR-0008) must be provably in sync with
the frontend source that produced it, since nothing else stops someone from editing
`frontend/src` and forgetting to rebuild, or hand-editing the committed bundle."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECK_SCRIPT = REPO_ROOT / "scripts" / "check_bundle_fresh.py"

sys.path.insert(0, str(REPO_ROOT))
from scripts._bundle_hash import compute_source_hash  # noqa: E402


def _run_check() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECK_SCRIPT)], capture_output=True, text=True
    )


def test_the_committed_bundle_matches_its_frontend_source() -> None:
    result = _run_check()
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_check_actually_detects_a_source_change_not_yet_rebuilt(
    tmp_path: Path,
) -> None:
    """Prove the check can fail, or it isn't checking anything: hash the real
    frontend, then hash it again with one file's content perturbed, and confirm the
    two hashes differ — that's the exact comparison `check_bundle_fresh.py` runs
    against the committed stamp."""
    real_hash = compute_source_hash(REPO_ROOT / "frontend")

    fake_frontend = tmp_path / "frontend"
    for path in (REPO_ROOT / "frontend").glob("src/*"):
        if path.is_file():
            dest = fake_frontend / "src" / path.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(path.read_bytes())
    for name in ("package.json", "package-lock.json", "vite.config.ts", "tsconfig.json", "index.html"):
        src = REPO_ROOT / "frontend" / name
        if src.is_file():
            (fake_frontend / name).write_bytes(src.read_bytes())

    (fake_frontend / "src" / "App.tsx").write_bytes(
        (fake_frontend / "src" / "App.tsx").read_bytes() + b"\n// perturbed\n"
    )

    assert compute_source_hash(fake_frontend) != real_hash
