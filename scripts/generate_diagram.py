"""Regenerate the architecture diagram from the live graph:
`python scripts/generate_diagram.py`.

Renders `graph.build_graph`'s actual node/edge structure as Mermaid (LangGraph's own
`draw_mermaid()` — pure Python, no network and no Graphviz needed) and splices it into
`README.md` between the `ARCHITECTURE_DIAGRAM` markers. The diagram can never drift
from the code that runs it, because it isn't drawn by hand — it's read off the same
`StateGraph` `run_invoice` executes.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from invoice_automation.deps import build_deps  # noqa: E402
from invoice_automation.graph import build_graph  # noqa: E402

README_PATH = REPO_ROOT / "README.md"
START_MARKER = "<!-- ARCHITECTURE_DIAGRAM:START -->"
END_MARKER = "<!-- ARCHITECTURE_DIAGRAM:END -->"


def render_mermaid() -> str:
    """The fake provider is enough here — the diagram is the graph's shape, which
    does not depend on which provider is wired in."""
    deps = build_deps(provider="fake")
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    try:
        graph = build_graph(deps, SqliteSaver(conn))
        return graph.get_graph().draw_mermaid()
    finally:
        conn.close()


def spliced_readme(readme_text: str, mermaid: str) -> str:
    start = readme_text.index(START_MARKER) + len(START_MARKER)
    end = readme_text.index(END_MARKER)
    if start > end:
        raise ValueError(f"{START_MARKER} must appear before {END_MARKER} in README.md")
    block = f"\n\n```mermaid\n{mermaid.strip()}\n```\n\n"
    return readme_text[:start] + block + readme_text[end:]


def main() -> int:
    if START_MARKER not in README_PATH.read_text(encoding="utf-8"):
        print(f"README.md has no {START_MARKER} marker to splice into.", file=sys.stderr)
        return 1

    mermaid = render_mermaid()
    readme_text = README_PATH.read_text(encoding="utf-8")
    README_PATH.write_text(spliced_readme(readme_text, mermaid), encoding="utf-8")
    print("Regenerated the architecture diagram in README.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
