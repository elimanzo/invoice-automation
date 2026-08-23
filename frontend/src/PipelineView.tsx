import { ConnectionStatus, PipelineRow, usePipelineRuns } from "./usePipelineRuns";
import { stageLabel } from "./stages";

function formatElapsed(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

function statusOf(row: PipelineRow): { label: string; className: string } {
  if (row.ok === false) return { label: "Failed", className: "status status--failed" };
  if (row.outcome === "rejected") return { label: "Rejected", className: "status status--rejected" };
  if (row.outcome === "approved") return { label: "Approved", className: "status status--approved" };
  if (row.awaitingReview) return { label: "Escalated", className: "status status--escalated" };
  if (row.running) return { label: "Processing", className: "status status--running" };
  return { label: "Unknown", className: "status" };
}

function ConnectionBanner({ status }: { status: ConnectionStatus }) {
  if (status === "live") return null;
  const message =
    status === "connecting" ? "Connecting to live updates…" : "Live updates lost — retrying, falling back to polling.";
  return <div className="banner banner--warning">{message}</div>;
}

export function PipelineView() {
  const { rows, status } = usePipelineRuns();

  return (
    <div className="view">
      <h1>Pipeline</h1>
      <p className="view__subtitle">
        Every invoice currently moving through ingestion, reconciliation, validation, and
        approval, updating live as each one progresses.
      </p>
      <ConnectionBanner status={status} />
      {rows.length === 0 ? (
        <p className="empty">
          No invoices yet. Process one from the command line, or point the API at a
          document or folder to get started.
        </p>
      ) : (
        <table className="pipeline-table">
          <thead>
            <tr>
              <th>Invoice</th>
              <th>Stage</th>
              <th>Elapsed</th>
              <th>Outcome</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const { label, className } = statusOf(row);
              return (
                <tr key={row.documentName}>
                  <td>{row.documentName}</td>
                  <td>{stageLabel(row.stage)}</td>
                  <td>{formatElapsed(row.elapsedMs)}</td>
                  <td>
                    <span className={className}>{label}</span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
