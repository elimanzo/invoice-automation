// Thin wrappers over the FastAPI endpoints in `invoice_automation.api` (ticket 13).
// Nothing here reimplements pipeline logic — every shape mirrors a Pydantic model
// on the server so the two stay in lockstep by construction.

export type RunSummary = {
  document_name: string;
  outcome: string | null;
  amount: string | null;
  currency: string | null;
  flag_count: number;
  timestamp: string | null;
  vendor: string | null;
  correction_count: number;
  max_flag_severity: string | null;
  failed_stage: string | null;
  failure_detail: string | null;
};

export type StageRecord = {
  stage: string;
  duration_ms: number;
  ok: boolean;
  detail: string | null;
};

export type LineItem = {
  item: string;
  quantity: number;
  unit_price: string | null;
  stated_amount: string | null;
  note: string | null;
  amount: string | null;
};

export type Invoice = {
  invoice_number: string | null;
  vendor: { name: string; address: string | null };
  invoice_date: string | null;
  due_date: string | null;
  line_items: LineItem[];
  subtotal: string | null;
  tax_amount: string | null;
  total: string | null;
  currency: string;
  payment_terms: string | null;
  purchase_order_reference: string | null;
  notes: string | null;
  revision: string | null;
};

export type Flag = {
  severity: "fatal" | "soft" | "info";
  code: string;
  message: string;
};

export type Correction = {
  field: string;
  raw: string;
  value: string;
  reason: string;
  confidence: number;
};

export type ToolCallRecord = {
  name: string;
  arguments: Record<string, unknown>;
  result: Record<string, unknown>;
};

export type LlmCallRecord = {
  kind: string;
  cache_hit: boolean;
  latency_ms: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: string;
  prompt: string;
  response: string;
};

export type HumanReview = {
  outcome: "approved" | "rejected";
  reason: string;
};

export type RunDetail = {
  document_name: string;
  document_format: string | null;
  document_path: string | null;
  raw_text: string | null;
  invoice: Invoice | null;
  flags: Flag[];
  decision: { outcome: string; reasoning: string } | null;
  corrections: Correction[];
  tool_calls: ToolCallRecord[];
  human_review: HumanReview | null;
  stages: StageRecord[];
  llm_calls: LlmCallRecord[];
  risk_score: number;
  awaiting_review: boolean;
};

export type StatusSummary = {
  provider: string;
  model: string;
};

export type ImpactSummary = {
  invoices_processed: number;
  avg_processing_ms: number;
  manual_baseline_days: number;
  errors_caught: number;
  dollars_flagged: string;
  cost_per_invoice_usd: string;
  manual_cost_per_invoice_usd: string;
};

export type StageTransitionEvent = {
  run_id: string;
  stage: string;
  transition: "enter" | "leave";
  ok?: boolean;
};

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

// The original document, byte-for-byte — a PDF renders as a PDF here rather than as
// its extracted text, which is what `raw_text` gives you and reads as garbled for a
// PDF or scanned document (SPEC.md #62).
export function sourceUrl(documentName: string): string {
  return `/runs/${encodeURIComponent(documentName)}/source`;
}

export function listReviews(): Promise<RunSummary[]> {
  return getJson<RunSummary[]>("/reviews");
}

export function getStatus(): Promise<StatusSummary> {
  return getJson<StatusSummary>("/status");
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
