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
  state: { completed_nodes?: string[]; errors?: string[] };
}

export const TOTAL_NODES = 7;

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
  connectors: () => request<Connector[]>("/connectors"),
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
