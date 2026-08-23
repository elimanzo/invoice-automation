// Mirrors models.py's FlagSeverity ordering (fatal > soft > info) so the ledger's sort
// and both the ledger and drill-down's badges agree on what "worse" means, without each
// component re-deriving it.
export const SEVERITY_RANK: Record<string, number> = { fatal: 3, soft: 2, info: 1 };

export function severityClassName(severity: string | null): string {
  if (severity === "fatal") return "severity severity--fatal";
  if (severity === "soft") return "severity severity--soft";
  if (severity === "info") return "severity severity--info";
  return "severity";
}

// Plain-language meaning of each severity (CONTEXT.md's definitions), shown as a
// tooltip on the badge so a controller or VP doesn't have to memorize the vocabulary.
export function severityTooltip(severity: string | null): string {
  if (severity === "fatal") return "Blocks payment automatically — this invoice was rejected.";
  if (severity === "soft") return "Doesn't block by itself, but adds to the invoice's risk score.";
  if (severity === "info") return "Recorded for visibility only — no effect on the decision.";
  return "No issues were flagged on this invoice.";
}
