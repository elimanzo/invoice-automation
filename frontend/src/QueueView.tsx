import { useEffect, useState } from "react";
import { RunDetail, RunSummary, getRun, listReviews, sourceUrl, submitReview } from "./api";
import { severityClassName, severityTooltip } from "./severity";
import { usePolled } from "./usePolled";

// Ticket 16: a VP's one screen for invoices that need judgement. Reviews are shown in
// full — extracted data, corrections, flags, risk score, and the plain reason the rules
// escalated it (`decision.reasoning`, from approval.py's `decide()`) — so a decision can
// be made without opening the source document, though it stays one click away.

// Mirrors config.py's RISK_ESCALATION_THRESHOLD — only used to color the badge, never to
// decide anything; the server has already made that call by the time an invoice reaches
// this queue.
const RISK_ESCALATION_THRESHOLD = 5;

function reviewDetails(summaries: RunSummary[]): Promise<RunDetail[]> {
  return Promise.all(summaries.map((s) => getRun(s.document_name)));
}

function ReviewCard({
  detail,
  onDecided,
}: {
  detail: RunDetail;
  onDecided: (documentName: string) => void;
}) {
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showSource, setShowSource] = useState(false);

  const invoice = detail.invoice;

  async function submit(outcome: "approved" | "rejected") {
    if (reason.trim() === "") {
      setError("A reason is required.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await submitReview(detail.document_name, outcome, reason.trim());
      onDecided(detail.document_name);
    } catch {
      setError("Could not submit the decision. Try again.");
      setSubmitting(false);
    }
  }

  return (
    <div className="review-card">
      <div className="review-card__header">
        <h2>{detail.document_name}</h2>
        <span
          className={`risk-score${detail.risk_score >= RISK_ESCALATION_THRESHOLD ? " risk-score--elevated" : ""}`}
          title="How much this invoice's combined flags concern the system — 5 or higher forces review on its own."
        >
          Risk score {detail.risk_score}
        </span>
      </div>

      {invoice && (
        <p className="review-card__vendor-line">
          <strong>{invoice.vendor.name}</strong> — {invoice.total ?? "—"} {invoice.currency}
          {invoice.invoice_number ? ` — ${invoice.invoice_number}` : ""}
        </p>
      )}

      {detail.decision && (
        <div className="review-card__why">
          <h3>Why this needs review</h3>
          <p>{detail.decision.reasoning}</p>
        </div>
      )}

      <h3>Flags ({detail.flags.length})</h3>
      {detail.flags.length === 0 ? (
        <p className="empty">No flags raised.</p>
      ) : (
        <ul className="flag-list">
          {detail.flags.map((flag, i) => (
            <li key={i}>
              <span className={severityClassName(flag.severity)} title={severityTooltip(flag.severity)}>
                {flag.severity}
              </span>{" "}
              <code>{flag.code}</code> — {flag.message}
            </li>
          ))}
        </ul>
      )}

      {detail.corrections.length > 0 && (
        <>
          <h3>Corrections ({detail.corrections.length})</h3>
          <table className="pipeline-table">
            <thead>
              <tr>
                <th>Field</th>
                <th>Raw</th>
                <th>Stored</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {detail.corrections.map((correction, i) => (
                <tr key={i}>
                  <td>{correction.field}</td>
                  <td>{correction.raw}</td>
                  <td>{correction.value}</td>
                  <td>{correction.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {invoice && invoice.line_items.length > 0 && (
        <>
          <h3>Line items</h3>
          <table className="pipeline-table">
            <thead>
              <tr>
                <th>Item</th>
                <th>Qty</th>
                <th>Unit price</th>
                <th>Amount</th>
              </tr>
            </thead>
            <tbody>
              {invoice.line_items.map((item, i) => (
                <tr key={i}>
                  <td>{item.item}</td>
                  <td>{item.quantity}</td>
                  <td>{item.unit_price ?? "—"}</td>
                  <td>{item.amount ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <button className="link-button" onClick={() => setShowSource((v) => !v)}>
        {showSource ? "Hide source document" : "View source document"}
      </button>
      {showSource &&
        (detail.document_format === "pdf" && detail.document_path ? (
          <>
            <iframe
              className="source-view source-view--pdf"
              title="Source document"
              src={sourceUrl(detail.document_name)}
            />
            <a
              className="source-view__open-tab"
              href={sourceUrl(detail.document_name)}
              target="_blank"
              rel="noreferrer"
            >
              Open in new tab
            </a>
          </>
        ) : (
          <pre className="source-view">{detail.raw_text ?? "(not available)"}</pre>
        ))}

      <div className="review-card__decision">
        <label htmlFor={`reason-${detail.document_name}`}>Your decision</label>
        <textarea
          id={`reason-${detail.document_name}`}
          placeholder="Why are you approving or rejecting this invoice?"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          rows={2}
        />
        {error && <p className="banner banner--warning">{error}</p>}
        <div className="review-card__buttons">
          <button className="btn btn--approve" disabled={submitting} onClick={() => submit("approved")}>
            Approve
          </button>
          <button className="btn btn--reject" disabled={submitting} onClick={() => submit("rejected")}>
            Reject
          </button>
        </div>
      </div>
    </div>
  );
}

export function QueueView() {
  const summaries = usePolled<RunSummary[]>(listReviews, 5000) ?? [];
  const [details, setDetails] = useState<RunDetail[]>([]);
  const documentNames = summaries.map((s) => s.document_name).join(",");

  useEffect(() => {
    let cancelled = false;
    reviewDetails(summaries).then((results) => {
      if (!cancelled) setDetails(results);
    });
    return () => {
      cancelled = true;
    };
    // Re-fetch only when queue membership changes, not on every poll tick.
  }, [documentNames]);

  function handleDecided(documentName: string) {
    setDetails((prev) => prev.filter((d) => d.document_name !== documentName));
  }

  return (
    <div className="view">
      <h1>Review queue</h1>
      <p className="view__subtitle">
        Only invoices the system can't decide on its own — everything you need to approve
        or reject is on the card, with the source document one click away.
      </p>
      {details.length === 0 ? (
        <p className="empty">Nothing needs your review right now.</p>
      ) : (
        <div className="review-list">
          {details.map((detail) => (
            <ReviewCard key={detail.document_name} detail={detail} onDecided={handleDecided} />
          ))}
        </div>
      )}
    </div>
  );
}
