import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { RunDetail, getRun } from "./api";
import { severityClassName } from "./severity";
import { stageLabel } from "./stages";

// Ticket 15: an engineer debugging one run. Reads the persisted trace via a single
// `GET /runs/{name}` — never re-runs the pipeline (SPEC.md #78).

export function RunDrilldown() {
  const { documentName } = useParams<{ documentName: string }>();
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!documentName) return;
    let cancelled = false;
    getRun(documentName)
      .then((data) => {
        if (!cancelled) setDetail(data);
      })
      .catch(() => {
        if (!cancelled) setError(`No such run: ${documentName}`);
      });
    return () => {
      cancelled = true;
    };
  }, [documentName]);

  if (error) {
    return (
      <div className="view">
        <p className="empty">{error}</p>
        <Link to="/ledger">Back to ledger</Link>
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="view">
        <p className="empty">Loading…</p>
      </div>
    );
  }

  return (
    <div className="view">
      <Link to="/ledger">&larr; Back to ledger</Link>
      <h1>{detail.document_name}</h1>

      {detail.decision && (
        <section>
          <h2>Decision</h2>
          <p>
            <span className="status">{detail.decision.outcome}</span>
          </p>
          <p>{detail.decision.reasoning}</p>
        </section>
      )}

      <section>
        <h2>Stages</h2>
        <table className="pipeline-table">
          <thead>
            <tr>
              <th>Stage</th>
              <th>Duration</th>
              <th>Result</th>
              <th>Detail</th>
            </tr>
          </thead>
          <tbody>
            {detail.stages.map((stage, i) => (
              <tr key={i}>
                <td>{stageLabel(stage.stage)}</td>
                <td>{stage.duration_ms.toFixed(1)} ms</td>
                <td>{stage.ok ? "ok" : "failed"}</td>
                <td>{stage.detail ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section>
        <h2>Flags ({detail.flags.length})</h2>
        {detail.flags.length === 0 ? (
          <p className="empty">No flags raised.</p>
        ) : (
          <ul className="flag-list">
            {detail.flags.map((flag, i) => (
              <li key={i}>
                <span className={severityClassName(flag.severity)}>{flag.severity}</span>{" "}
                <code>{flag.code}</code> — {flag.message}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h2>Corrections ({detail.corrections.length})</h2>
        {detail.corrections.length === 0 ? (
          <p className="empty">No corrections made.</p>
        ) : (
          <table className="pipeline-table">
            <thead>
              <tr>
                <th>Field</th>
                <th>Raw</th>
                <th>Stored</th>
                <th>Reason</th>
                <th>Confidence</th>
              </tr>
            </thead>
            <tbody>
              {detail.corrections.map((correction, i) => (
                <tr key={i}>
                  <td>{correction.field}</td>
                  <td>{correction.raw}</td>
                  <td>{correction.value}</td>
                  <td>{correction.reason}</td>
                  <td>{(correction.confidence * 100).toFixed(0)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section>
        <h2>LLM calls ({detail.llm_calls.length})</h2>
        {detail.llm_calls.length === 0 ? (
          <p className="empty">No LLM calls made.</p>
        ) : (
          <table className="pipeline-table">
            <thead>
              <tr>
                <th>Kind</th>
                <th>Cache</th>
                <th>Latency</th>
                <th>Prompt tokens</th>
                <th>Completion tokens</th>
                <th>Cost</th>
              </tr>
            </thead>
            <tbody>
              {detail.llm_calls.map((call, i) => (
                <tr key={i}>
                  <td>{call.kind}</td>
                  <td>{call.cache_hit ? "hit" : "miss"}</td>
                  <td>{call.latency_ms.toFixed(0)} ms</td>
                  <td>{call.prompt_tokens}</td>
                  <td>{call.completion_tokens}</td>
                  <td>${Number(call.cost_usd).toFixed(4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="drilldown-columns">
        <div>
          <h2>Source document</h2>
          <pre className="source-view">{detail.raw_text ?? "(not available)"}</pre>
        </div>
        <div>
          <h2>Extracted invoice</h2>
          <pre className="source-view">{JSON.stringify(detail.invoice, null, 2)}</pre>
        </div>
      </section>
    </div>
  );
}
