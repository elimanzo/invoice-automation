import { useEffect, useState } from "react";
import { StatusSummary, getStatus } from "./api";

// Which reasoning engine answered the invoices on screen. Fetched once per page load,
// not polled — the provider is fixed for the process's lifetime (deps.py's
// `provider_name`), it never changes mid-session.
export function StatusBadge() {
  const [status, setStatus] = useState<StatusSummary | null>(null);

  useEffect(() => {
    getStatus()
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);

  if (!status) return null;

  const isFake = status.provider === "fake";
  return (
    <div className={`status-badge ${isFake ? "status-badge--fake" : "status-badge--live"}`}>
      <span className="status-badge__dot" />
      {isFake ? "Fake provider (no API key)" : `${status.provider} · ${status.model}`}
    </div>
  );
}
