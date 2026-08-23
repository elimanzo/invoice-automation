// Thin wrappers over the FastAPI endpoints in `invoice_automation.api` (ticket 13).
// Nothing here reimplements pipeline logic — every shape mirrors a Pydantic model
// on the server so the two stay in lockstep by construction.

export interface RunSummary {
  document_name: string;
  outcome: string | null;
  amount: string | null;
  currency: string | null;
  flag_count: number;
  timestamp: string | null;
  vendor: string | null;
  correction_count: number;
  max_flag_severity: string | null;
}

export interface StageRecord {
  stage: string;
  duration_ms: number;
  ok: boolean;
  detail: string | null;
}

export interface Flag {
  severity: "fatal" | "soft" | "info";
  code: string;
  message: string;
}

export interface Correction {
  field: string;
  raw: string;
  value: string;
  reason: string;
  confidence: number;
}

export interface ToolCallRecord {
  name: string;
  arguments: Record<string, unknown>;
  result: Record<string, unknown>;
}

export interface LlmCallRecord {
  kind: string;
  cache_hit: boolean;
  latency_ms: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: string;
  prompt: string;
  response: string;
}

export interface RunDetail {
  document_name: string;
  document_format: string | null;
  raw_text: string | null;
  invoice: unknown;
  flags: Flag[];
  decision: { outcome: string; reasoning: string } | null;
  corrections: Correction[];
  tool_calls: ToolCallRecord[];
  human_review: unknown;
  stages: StageRecord[];
  llm_calls: LlmCallRecord[];
  awaiting_review: boolean;
}

export interface ImpactSummary {
  invoices_processed: number;
  avg_processing_ms: number;
  manual_baseline_days: number;
  errors_caught: number;
  dollars_flagged: string;
  cost_per_invoice_usd: string;
  manual_cost_per_invoice_usd: string;
}

export interface StageTransitionEvent {
  run_id: string;
  stage: string;
  transition: "enter" | "leave";
  ok?: boolean;
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(`${path} responded ${response.status}`);
  }
  return (await response.json()) as T;
}

export function listRuns(): Promise<RunSummary[]> {
  return getJson<RunSummary[]>("/runs");
}

export function getRun(documentName: string): Promise<RunDetail> {
  return getJson<RunDetail>(`/runs/${encodeURIComponent(documentName)}`);
}

export function listReviews(): Promise<RunSummary[]> {
  return getJson<RunSummary[]>("/reviews");
}

export function getImpact(): Promise<ImpactSummary> {
  return getJson<ImpactSummary>("/impact");
}

export function submitReview(
  documentName: string,
  outcome: "approved" | "rejected",
  reason: string,
): Promise<RunDetail> {
  return fetch(`/reviews/${encodeURIComponent(documentName)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ outcome, reason }),
  }).then(async (response) => {
    if (!response.ok) {
      throw new Error(`submit review responded ${response.status}`);
    }
    return (await response.json()) as RunDetail;
  });
}
