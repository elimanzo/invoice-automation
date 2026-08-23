// Shared decision-outcome -> badge-class mapping (approved/rejected/escalated), so the
// ledger and the run drill-down color the same outcome the same way.
export function decisionClassName(outcome: string | null | undefined): string {
  if (outcome === "approved") return "status status--approved";
  if (outcome === "rejected") return "status status--rejected";
  if (outcome === "escalated") return "status status--escalated";
  return "status";
}
