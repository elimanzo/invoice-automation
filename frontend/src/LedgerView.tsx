import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { RunSummary, listRuns } from "./api";
import { ImpactStrip } from "./ImpactStrip";
import { SEVERITY_RANK, severityClassName } from "./severity";
import { usePolled } from "./usePolled";

type SortKey = "document_name" | "vendor" | "amount" | "outcome" | "max_flag_severity";
type SortDirection = "asc" | "desc";

const DECISION_OPTIONS = ["all", "approved", "rejected", "escalated"] as const;
const SEVERITY_OPTIONS = ["all", "fatal", "soft", "info", "none"] as const;

function severityLabel(severity: string | null): string {
  return severity ?? "none";
}

function decisionClassName(outcome: string | null): string {
  if (outcome === "approved") return "status status--approved";
  if (outcome === "rejected") return "status status--rejected";
  if (outcome === "escalated") return "status status--escalated";
  return "status";
}

export function LedgerView() {
  const runs = usePolled<RunSummary[]>(listRuns, 5000) ?? [];
  const [decisionFilter, setDecisionFilter] = useState<(typeof DECISION_OPTIONS)[number]>("all");
  const [vendorFilter, setVendorFilter] = useState("");
  const [severityFilter, setSeverityFilter] = useState<(typeof SEVERITY_OPTIONS)[number]>("all");
  const [minAmount, setMinAmount] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("document_name");
  const [sortDirection, setSortDirection] = useState<SortDirection>("asc");

  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDirection((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDirection("asc");
    }
  }

  const rows = useMemo(() => {
    const minAmountValue = minAmount.trim() === "" ? null : Number(minAmount);
    const filtered = runs.filter((run) => {
      if (decisionFilter !== "all" && run.outcome !== decisionFilter) return false;
      if (vendorFilter.trim() !== "") {
        const vendor = (run.vendor ?? "").toLowerCase();
        if (!vendor.includes(vendorFilter.trim().toLowerCase())) return false;
      }
      if (severityFilter !== "all") {
        const severity = run.max_flag_severity ?? "none";
        if (severity !== severityFilter) return false;
      }
      if (minAmountValue !== null && !Number.isNaN(minAmountValue)) {
        const amount = run.amount === null ? null : Number(run.amount);
        if (amount === null || amount < minAmountValue) return false;
      }
      return true;
    });

    const direction = sortDirection === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => {
      let cmp = 0;
      switch (sortKey) {
        case "document_name":
          cmp = a.document_name.localeCompare(b.document_name);
          break;
        case "vendor":
          cmp = (a.vendor ?? "").localeCompare(b.vendor ?? "");
          break;
        case "amount":
          cmp = (a.amount === null ? -Infinity : Number(a.amount)) -
            (b.amount === null ? -Infinity : Number(b.amount));
          break;
        case "outcome":
          cmp = (a.outcome ?? "").localeCompare(b.outcome ?? "");
          break;
        case "max_flag_severity":
          cmp = (SEVERITY_RANK[a.max_flag_severity ?? ""] ?? 0) -
            (SEVERITY_RANK[b.max_flag_severity ?? ""] ?? 0);
          break;
      }
      return cmp * direction;
    });
  }, [runs, decisionFilter, vendorFilter, severityFilter, minAmount, sortKey, sortDirection]);

  function headerButton(label: string, key: SortKey) {
    const active = sortKey === key;
    return (
      <button className="sort-button" onClick={() => toggleSort(key)}>
        {label}
        {active ? (sortDirection === "asc" ? " ▲" : " ▼") : ""}
      </button>
    );
  }

  return (
    <div className="view">
      <h1>Ledger</h1>
      <ImpactStrip />

      <div className="ledger-filters">
        <label>
          Decision
          <select
            value={decisionFilter}
            onChange={(e) => setDecisionFilter(e.target.value as (typeof DECISION_OPTIONS)[number])}
          >
            {DECISION_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </label>
        <label>
          Vendor
          <input
            type="text"
            value={vendorFilter}
            onChange={(e) => setVendorFilter(e.target.value)}
            placeholder="Filter by vendor"
          />
        </label>
        <label>
          Min amount
          <input
            type="number"
            value={minAmount}
            onChange={(e) => setMinAmount(e.target.value)}
            placeholder="0"
          />
        </label>
        <label>
          Flag severity
          <select
            value={severityFilter}
            onChange={(e) => setSeverityFilter(e.target.value as (typeof SEVERITY_OPTIONS)[number])}
          >
            {SEVERITY_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </label>
      </div>

      {rows.length === 0 ? (
        <p className="empty">No processed invoices match these filters.</p>
      ) : (
        <table className="pipeline-table">
          <thead>
            <tr>
              <th>{headerButton("Invoice", "document_name")}</th>
              <th>{headerButton("Vendor", "vendor")}</th>
              <th>{headerButton("Amount", "amount")}</th>
              <th>{headerButton("Decision", "outcome")}</th>
              <th>{headerButton("Worst flag", "max_flag_severity")}</th>
              <th>Corrections</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((run) => (
              <tr key={run.document_name}>
                <td>
                  <Link to={`/ledger/${encodeURIComponent(run.document_name)}`}>
                    {run.document_name}
                  </Link>
                </td>
                <td>{run.vendor ?? "—"}</td>
                <td>
                  {run.amount ?? "—"} {run.currency ?? ""}
                </td>
                <td>
                  <span className={decisionClassName(run.outcome)}>{run.outcome ?? "pending"}</span>
                </td>
                <td>
                  <span className={severityClassName(run.max_flag_severity)}>
                    {severityLabel(run.max_flag_severity)}
                  </span>
                </td>
                <td>{run.correction_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
