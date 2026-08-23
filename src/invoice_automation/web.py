"""Entry point for the dashboard (ticket 14).

`python -m invoice_automation.web` is the whole story per ADR-0008: build the API app
(ticket 13), mount the committed React bundle as static files alongside it, and serve
both from one uvicorn process. No Node toolchain is required to run this — only to
rebuild `static/` with `npm run build` inside `frontend/`.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.sqlite import SqliteSaver

from .api import create_app
from .config import MissingApiKey, Settings
from .deps import build_deps
from .structured_logging import configure_logging

CHECKPOINT_FILENAME = "checkpoints.db"
STATIC_DIR = Path(__file__).parent / "static"


def build_app() -> FastAPI:
    configure_logging()
    settings = Settings.from_env()
    try:
        deps = build_deps(settings)
    except MissingApiKey as exc:
        raise SystemExit(f"error: {exc}") from exc

    checkpoint_path = Path(settings.data_dir) / CHECKPOINT_FILENAME
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(checkpoint_path, check_same_thread=False)
    checkpointer = SqliteSaver(conn)

    uploads_dir = Path(settings.data_dir) / "uploads"
    app = create_app(deps, checkpointer, uploads_dir)
    # Mounted last: routes `create_app` already registered (/runs, /events, ...) are
    # matched first, so the SPA fallback below only ever catches dashboard paths.
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="dashboard")
    return app


def main() -> None:
    uvicorn.run(build_app(), host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
