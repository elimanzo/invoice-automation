// Shared decision-outcome -> badge-class mapping (approved/rejected/escalated), so the
// ledger and the run drill-down color the same outcome the same way.
export function decisionClassName(outcome: string | null | undefined): string {
  if (outcome === "approved") return "status status--approved";
  if (outcome === "rejected") return "status status--rejected";
  if (outcome === "escalated") return "status status--escalated";
  return "status";
}

// A run with no outcome yet is either genuinely untouched, or it errored partway
// through (e.g. a missing LLM cassette, or a revision-after-payment conflict) — those
// read very differently to a reviewer and shouldn't share one "pending" label.
export function runStatusLabel(run: {
  outcome: string | null;
  failed_stage: string | null;
}): string {
  if (run.outcome) return run.outcome;
  if (run.failed_stage) return `failed (${run.failed_stage})`;
  return "pending";
}

export function runStatusClassName(run: {
  outcome: string | null;
  failed_stage: string | null;
}): string {
  if (run.failed_stage && !run.outcome) return "status status--failed";
  return decisionClassName(run.outcome);
}
