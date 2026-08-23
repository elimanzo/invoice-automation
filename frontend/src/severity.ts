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
