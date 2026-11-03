export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Category = "confirmed_demand" | "verified_partnership" | "speculative_prospect";

export interface Opportunity {
  id: string;
  category: Category;
  title: string;
  status: string;
  verification_status: string;
  confidence: number;
  last_seen_at: string;
}

export interface Run {
  id: string;
  kind: string;
  status: string;
  attempts: number;
  error: string | null;
  created_at: string;
  state: { completed_nodes?: string[]; errors?: string[]; connector_stats?: ConnectorStat[] };
}

export interface ConnectorStat {
  connector: string;
  is_mock: boolean;
  status: string;
  retrieved: number;
  rejected: number;
  created: number;
  duplicates: number;
  errors: number;
  cost_usd: number;
}

export interface BoardRow {
  id: string;
  category: Category;
  title: string;
  status: string;
  verification_status: string;
  confidence: number;
  organization: string | null;
  organization_kind: string | null;
  country: string | null;
  domain: string | null;
  is_mock: boolean;
  score: number | null;
  qualification_rationale: string | null;
  evidence_count: number;
  source_url: string | null;
  published_at: string | null;
  stated_budget: string | null;
  ai_summary: string | null;
  next_action: string;
  last_seen_at: string;
}

export interface CostSummary {
  lines: { family: string; spent_usd: number; cap_usd: number | null; remaining_usd: number | null }[];
  total_spent_usd: number;
  total_cap_usd: number | null;
  today_usd: number;
  month_usd: number;
  note: string;
}

export interface OpportunityDetail {
  id: string;
  title: string;
  evidence: (Evidence & { is_mock: boolean; retrieved_at: string })[];
}

export interface Analysis {
  id: string;
  task: string;
  provider: string;
  model: string;
  validated: boolean;
  rejection_reason: string | null;
  output: Record<string, unknown> | null;
}

export const TOTAL_NODES = 8;

export interface Evidence {
  id: string;
  run_id: string | null;
  provider: string;
  source_url: string;
  original_text: string;
  published_at: string | null;
  confidence: number;
  verification_status: string;
  source_check_status: string;
}

export interface Connector {
  name: string;
  enabled: boolean;
  is_mock: boolean;
  paid: boolean;
  usable: boolean;
  paid_execution_approved: boolean | null;
  actor_id: string | null;
  schema_validated_on: string | null;
  notes: string;
}

export interface Profile {
  id: string;
  name: string;
  keywords: string[];
  regions: string[];
  connector_names: string[];
  max_results: number;
  enabled: boolean;
}

export interface AiStatus {
  provider: string;
  mode: "mock" | "live";
  paid_calls_enabled: boolean;
  api_key_configured: boolean;
  model: string | null;
  prices_configured: boolean;
  would_make_paid_calls: boolean;
  per_run_llm_limit_usd: number;
}

export interface AiUsage {
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  calls: number;
  by_task: { task: string; outcome: string; calls: number; cost_usd: number }[];
}

export function runProgress(r: Run): number {
  return Math.min(100, Math.round(((r.state.completed_nodes?.length ?? 0) / TOTAL_NODES) * 100));
}

export interface Draft {
  id: string;
  opportunity_id: string;
  subject: string | null;
  body: string;
  status: string;
}

export interface Metrics {
  opportunities_by_category: Record<string, number>;
  opportunities_by_status: Record<string, number>;
  drafts_pending_approval: number;
  total_provider_spend_usd: number;
}

export const CATEGORY_LABEL: Record<Category, string> = {
  confirmed_demand: "Confirmed project demand",
  verified_partnership: "Verified partnership activity",
  speculative_prospect: "Speculative prospects",
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    cache: "no-store",
    headers: { "content-type": "application/json", ...init?.headers },
  });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return (await res.json()) as T;
}

export const api = {
  metrics: () => request<Metrics>("/metrics"),
  runs: () => request<Run[]>("/runs"),
  opportunities: () => request<Opportunity[]>("/opportunities"),
  drafts: () => request<Draft[]>("/drafts?status=pending_approval"),
  startRun: (profileId?: string) =>
    request<Run>("/runs", {
      method: "POST",
      body: JSON.stringify(profileId ? { profile_id: profileId } : {}),
    }),
  evidence: () => request<Evidence[]>("/evidence"),
  aiStatus: () => request<AiStatus>("/ai/status"),
  aiUsage: () => request<AiUsage>("/ai/usage"),
  connectors: () => request<Connector[]>("/connectors"),
  board: () => request<BoardRow[]>("/board"),
  costs: () => request<CostSummary>("/costs"),
  cancelRun: (id: string) => request<Run>(`/runs/${id}/cancel`, { method: "POST" }),
  opportunity: (id: string) => request<OpportunityDetail>(`/opportunities/${id}`),
  analyses: (id: string) => request<Analysis[]>(`/opportunities/${id}/analyses`),
  profiles: () => request<Profile[]>("/search-profiles"),
};

export function groupByCategory(items: Opportunity[]): Record<Category, Opportunity[]> {
  const out: Record<Category, Opportunity[]> = {
    confirmed_demand: [],
    verified_partnership: [],
    speculative_prospect: [],
  };
  for (const o of items) out[o.category]?.push(o);
  return out;
}

/** Real leads only: records whose every piece of evidence came from a mock provider are fixtures. */
export function splitReal(rows: BoardRow[]): { real: BoardRow[]; mock: BoardRow[] } {
  return { real: rows.filter((r) => !r.is_mock), mock: rows.filter((r) => r.is_mock) };
}

export interface JobTotals {
  retrieved: number;
  rejected: number;
  duplicates: number;
  errors: number;
  cost_usd: number;
}

export function jobTotals(run: Run): JobTotals {
  const t: JobTotals = { retrieved: 0, rejected: 0, duplicates: 0, errors: 0, cost_usd: 0 };
  for (const s of run.state.connector_stats ?? []) {
    t.retrieved += s.retrieved;
    t.rejected += s.rejected;
    t.duplicates += s.duplicates;
    t.errors += s.errors;
    t.cost_usd += s.cost_usd;
  }
  return t;
}
