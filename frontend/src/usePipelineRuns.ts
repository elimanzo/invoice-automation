import { useEffect, useMemo, useRef, useState } from "react";
import { RunDetail, StageTransitionEvent, getRun, listRuns } from "./api";

export type PipelineRow = {
  documentName: string;
  stage: string;
  running: boolean;
  ok: boolean | null;
  awaitingReview: boolean;
  outcome: string | null;
  amount: string | null;
  currency: string | null;
  elapsedMs: number;
};

export type ConnectionStatus = "connecting" | "live" | "reconnecting";

// A poll interval that keeps the view moving even if the SSE connection never
// recovers — the ticket's "degrades gracefully rather than freezing" requirement.
const FALLBACK_POLL_MS = 5000;
const TICK_MS = 250;

function rowFromDetail(detail: RunDetail): PipelineRow {
  const completedMs = detail.stages.reduce((sum, s) => sum + s.duration_ms, 0);
  const last = detail.stages[detail.stages.length - 1];
  const failed = last ? !last.ok : false;
  return {
    documentName: detail.document_name,
    stage: last ? last.stage : "ingest",
    running: !failed && !detail.decision && !detail.awaiting_review,
    ok: failed ? false : null,
    awaitingReview: detail.awaiting_review,
    outcome: detail.decision?.outcome ?? null,
    amount: null,
    currency: null,
    elapsedMs: completedMs,
  };
}

export function usePipelineRuns(): { rows: PipelineRow[]; status: ConnectionStatus } {
  const [rows, setRows] = useState<Map<string, PipelineRow>>(new Map());
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  // Wall-clock anchor for the stage currently in flight, per run — used only to tick
  // the live elapsed-time display; never sent anywhere.
  const runningSince = useRef<Map<string, number>>(new Map());

  useEffect(() => {
    let cancelled = false;

    async function refreshRun(documentName: string) {
      try {
        const detail = await getRun(documentName);
        if (cancelled) return;
        const merged = rowFromDetail(detail);
        setRows((prev) => {
          const next = new Map(prev);
          const existing = prev.get(documentName);
          next.set(documentName, {
            ...merged,
            amount: existing?.amount ?? merged.amount,
            currency: existing?.currency ?? merged.currency,
          });
          return next;
        });
        if (merged.running) {
          runningSince.current.set(documentName, Date.now());
        } else {
          runningSince.current.delete(documentName);
        }
      } catch {
        // A single run failing to refresh should not take down the whole view.
      }
    }

    async function seed() {
      try {
        const summaries = await listRuns();
        if (cancelled) return;
        setRows((prev) => {
          const next = new Map(prev);
          for (const summary of summaries) {
            if (!next.has(summary.document_name)) {
              next.set(summary.document_name, {
                documentName: summary.document_name,
                stage: "ingest",
                running: false,
                ok: null,
                awaitingReview: false,
                outcome: summary.outcome,
                amount: summary.amount,
                currency: summary.currency,
                elapsedMs: 0,
              });
            }
          }
          return next;
        });
        await Promise.all(summaries.map((s) => refreshRun(s.document_name)));
      } catch {
        // Seeding failure just leaves the view empty; the poller below retries.
      }
    }

    seed();
    const poller = setInterval(seed, FALLBACK_POLL_MS);

    const source = new EventSource("/events");
    source.onopen = () => {
      if (!cancelled) setStatus("live");
    };
    source.onerror = () => {
      if (!cancelled) setStatus("reconnecting");
    };
    source.onmessage = (message) => {
      if (cancelled) return;
      const event: StageTransitionEvent = JSON.parse(message.data);
      setRows((prev) => {
        const next = new Map(prev);
        const existing = next.get(event.run_id) ?? {
          documentName: event.run_id,
          stage: event.stage,
          running: true,
          ok: null,
          awaitingReview: false,
          outcome: null,
          amount: null,
          currency: null,
          elapsedMs: 0,
        };
        if (event.transition === "enter") {
          runningSince.current.set(event.run_id, Date.now());
          next.set(event.run_id, { ...existing, stage: event.stage, running: true, ok: null });
        } else {
          const since = runningSince.current.get(event.run_id) ?? Date.now();
          runningSince.current.delete(event.run_id);
          next.set(event.run_id, {
            ...existing,
            stage: event.stage,
            running: false,
            ok: event.ok ?? true,
            elapsedMs: existing.elapsedMs + (Date.now() - since),
          });
        }
        return next;
      });
      refreshRun(event.run_id);
    };

    const ticker = setInterval(() => {
      if (runningSince.current.size > 0) {
        // Force a re-render so elapsed time visibly advances for running stages;
        // the actual duration is recomputed from `runningSince` at render time.
        setRows((prev) => new Map(prev));
      }
    }, TICK_MS);

    return () => {
      cancelled = true;
      source.close();
      clearInterval(poller);
      clearInterval(ticker);
    };
  }, []);

  const liveRows = useMemo(() => {
    return Array.from(rows.values())
      .map((row) => {
        const since = runningSince.current.get(row.documentName);
        const liveElapsed = row.running && since ? row.elapsedMs + (Date.now() - since) : row.elapsedMs;
        return { ...row, elapsedMs: liveElapsed };
      })
      .sort((a, b) => a.documentName.localeCompare(b.documentName));
  }, [rows]);

  return { rows: liveRows, status };
}
