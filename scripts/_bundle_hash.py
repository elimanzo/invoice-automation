"""Shared by `rebuild_dashboard.py` (writes the stamp) and `check_bundle_fresh.py`
(reads it back) — one hash function, so there is no way for the two to drift apart on
what "the source" even means.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

# Everything that actually changes what `vite build` produces. Deliberately excludes
# node_modules, dist output, and editor/lockfile noise that doesn't affect the bundle.
SOURCE_GLOBS = ("src/**/*", "package.json", "package-lock.json", "vite.config.ts", "tsconfig.json", "index.html")

STAMP_FILENAME = ".bundle-source-hash"


def compute_source_hash(frontend_dir: Path) -> str:
    digest = hashlib.sha256()
    files: list[Path] = []
    for pattern in SOURCE_GLOBS:
        files.extend(p for p in frontend_dir.glob(pattern) if p.is_file())
    for path in sorted(set(files)):
        digest.update(path.relative_to(frontend_dir).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()
