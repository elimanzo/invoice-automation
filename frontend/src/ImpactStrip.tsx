import { ImpactSummary, getImpact } from "./api";
import { usePolled } from "./usePolled";

// Ticket 15: the controller-facing business-impact strip. States the system in the
// terms the business uses (REQUIREMENTS.md, SPEC.md #76) rather than pipeline
// vocabulary — the manual-process baselines it compares against come from the server
// (config.py), never hardcoded here.
function formatSeconds(ms: number): string {
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatUsd(value: string): string {
  const n = Number(value);
  return Number.isFinite(n) ? `$${n.toFixed(2)}` : value;
}

export function ImpactStrip() {
  const impact = usePolled<ImpactSummary>(getImpact, 5000);

  if (!impact) return null;

  return (
    <div className="impact-strip">
      <div className="impact-stat">
        <div className="impact-stat__value">{impact.invoices_processed}</div>
        <div className="impact-stat__label">Invoices processed</div>
      </div>
      <div className="impact-stat">
        <div className="impact-stat__value">{formatSeconds(impact.avg_processing_ms)}</div>
        <div className="impact-stat__label">
          Avg. processing time <span className="impact-stat__baseline">vs. {impact.manual_baseline_days}-day manual baseline</span>
        </div>
      </div>
      <div className="impact-stat">
        <div className="impact-stat__value">{impact.errors_caught}</div>
        <div className="impact-stat__label">Errors caught</div>
      </div>
      <div className="impact-stat">
        <div className="impact-stat__value">{formatUsd(impact.dollars_flagged)}</div>
        <div className="impact-stat__label">Dollars flagged before payment</div>
      </div>
      <div className="impact-stat">
        <div className="impact-stat__value">{formatUsd(impact.cost_per_invoice_usd)}</div>
        <div className="impact-stat__label">
          Cost per invoice{" "}
          <span className="impact-stat__baseline">
            vs. {formatUsd(impact.manual_cost_per_invoice_usd)} manual (est.)
          </span>
        </div>
      </div>
    </div>
  );
}
