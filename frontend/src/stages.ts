// Maps the graph's internal node names (graph.py) to the vocabulary CONTEXT.md defines,
// so the dashboard reads in the same terms as the rest of the codebase and its docs.
export const STAGE_LABELS: Record<string, string> = {
  ingest: "Ingestion",
  reconcile: "Reconciliation",
  validate: "Validation",
  approve: "Approval",
  await_review: "Escalation",
  pay: "Payment",
};

export function stageLabel(stage: string): string {
  return STAGE_LABELS[stage] ?? stage;
}
