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
}

export interface StageRecord {
  stage: string;
  duration_ms: number;
  ok: boolean;
  detail: string | null;
}

export interface RunDetail {
  document_name: string;
  invoice: unknown;
  flags: unknown[];
  decision: { outcome: string; reasoning: string } | null;
  corrections: unknown[];
  tool_calls: unknown[];
  human_review: unknown;
  stages: StageRecord[];
  awaiting_review: boolean;
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
