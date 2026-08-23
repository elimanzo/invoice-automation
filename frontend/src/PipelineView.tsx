import { useRef, useState } from "react";
import { ConnectionStatus, PipelineRow, usePipelineRuns } from "./usePipelineRuns";
import { stageLabel } from "./stages";
import { UploadRejection, uploadDocuments } from "./api";

// Ticket 20: mirrors documents.py's SUPPORTED_EXTENSIONS — kept in sync by hand since
// the file picker's `accept` attribute is advisory only (the server is the real check).
const ACCEPTED_EXTENSIONS = ".txt,.json,.csv,.xml,.pdf";

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

function UploadControl() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [rejected, setRejected] = useState<UploadRejection[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function handleFiles(fileList: FileList | null) {
    if (!fileList || fileList.length === 0) return;
    setUploading(true);
    setError(null);
    setRejected([]);
    try {
      const result = await uploadDocuments(Array.from(fileList));
      setRejected(result.rejected);
    } catch {
      setError("Upload failed. Check your connection and try again.");
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  return (
    <div className="upload-control">
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={ACCEPTED_EXTENSIONS}
        onChange={(e) => handleFiles(e.target.files)}
        className="upload-control__input"
        id="upload-input"
      />
      <label htmlFor="upload-input" className="btn btn--upload">
        {uploading ? "Uploading…" : "Upload invoice files"}
      </label>
      {error && <p className="banner banner--warning">{error}</p>}
      {rejected.length > 0 && (
        <ul className="banner banner--warning upload-control__rejections">
          {rejected.map((r) => (
            <li key={r.filename}>
              <strong>{r.filename}</strong>: {r.reason}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function PipelineView() {
  const { rows, status } = usePipelineRuns();

  return (
    <div className="view">
      <h1>Processing</h1>
      <p className="view__subtitle">
        Every invoice currently being read, checked, and approved, updating live as each
        one moves along.
      </p>
      <ConnectionBanner status={status} />
      <UploadControl />
      {rows.length === 0 ? (
        <p className="empty">
          Click to upload invoice files. New invoices will appear below once they're
          submitted.
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
                    {row.failureDetail && (
                      <span className="pipeline-table__reason" title={row.failureDetail}>
                        {row.failureDetail}
                      </span>
                    )}
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
