"""Reset one invoice's processing history so it can be reprocessed from scratch.

`python main.py --invoice_path=...` always starts a run from `ingest` -- there's no
"resume where a failed run left off" state to clean up for a run that never paid. But
a run that already reached payment is deliberately NOT safe to just rerun:
reconciliation flags the resubmission "identical, already seen" and a second payment
attempt is refused (the paid-once guard in registry.py) -- both correct, on purpose,
protecting against double payment. This script is for when you actually want a clean
slate during local testing: it clears exactly one document's history from the three
places it's recorded, and nothing else's.

    python scripts/reset_run.py --document invoice_1013.pdf
    python scripts/reset_run.py --document invoice_1013.pdf --yes   # skip the prompt

Clears:
  - `checkpoints.db` -- the graph's run state, which the dashboard's drilldown reads
  - `trace.db`        -- the stage timings and LLM call log for that run
  - `registry.db`     -- the "already seen" record, and the payment record if the
                         invoice this document names was ever actually paid

Does not touch the catalogue, the LLM cache, or any other document's history.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from invoice_automation.cli import CHECKPOINT_FILENAME  # noqa: E402
from invoice_automation.config import Settings  # noqa: E402
from invoice_automation.deps import build_deps  # noqa: E402
from invoice_automation.graph import build_graph  # noqa: E402
from invoice_automation.registry import normalize_invoice_identity  # noqa: E402
from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: E402


def _invoice_identity_for(document_name: str, deps, checkpointer) -> str | None:
    """Read the run's own checkpoint for its invoice number, normalized the same way
    `_pay` keys `payments` on (registry.py's `normalize_invoice_identity` -- "INV-1001"
    and a bare "1001" are the same identity) -- so a payment made under it can be
    found and cleared too. Returns None for a run that never got as far as extraction.
    """
    graph = build_graph(deps, checkpointer)
    snapshot = graph.get_state({"configurable": {"thread_id": document_name}})
    invoice = (snapshot.values or {}).get("invoice")
    if not invoice:
        return None
    return normalize_invoice_identity(invoice.get("invoice_number"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--document", required=True, help="document name, e.g. invoice_1013.pdf")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    data_dir = Path(settings.data_dir)
    checkpoints_path = data_dir / CHECKPOINT_FILENAME
    trace_path = data_dir / "trace.db"
    registry_path = data_dir / settings.registry_filename

    deps = build_deps(settings)

    identity = None
    if checkpoints_path.is_file():
        with closing(sqlite3.connect(checkpoints_path, check_same_thread=False)) as conn:
            identity = _invoice_identity_for(args.document, deps, SqliteSaver(conn))

    print(f"About to reset {args.document!r}", end="")
    print(f" (invoice identity {identity})" if identity else " (no invoice extracted yet)")
    if not args.yes:
        reply = input("Proceed? [y/N] ").strip().lower()
        if reply != "y":
            print("Aborted.")
            return 1

    if checkpoints_path.is_file():
        with closing(sqlite3.connect(checkpoints_path)) as conn, conn:
            conn.execute("DELETE FROM checkpoints WHERE thread_id = ?", (args.document,))
            conn.execute("DELETE FROM writes WHERE thread_id = ?", (args.document,))
        print(f"cleared checkpoint state in {checkpoints_path}")

    if trace_path.is_file():
        with closing(sqlite3.connect(trace_path)) as conn, conn:
            conn.execute("DELETE FROM stage_events WHERE run_id = ?", (args.document,))
            conn.execute("DELETE FROM llm_call_events WHERE run_id = ?", (args.document,))
        print(f"cleared trace in {trace_path}")

    if registry_path.is_file():
        with closing(sqlite3.connect(registry_path)) as conn, conn:
            conn.execute("DELETE FROM seen_invoices WHERE document_name = ?", (args.document,))
            if identity:
                conn.execute("DELETE FROM payments WHERE invoice_number = ?", (identity,))
        print(f"cleared registry entries in {registry_path}")

    print(f"Done. Rerun {args.document!r} to reprocess it as if never seen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
