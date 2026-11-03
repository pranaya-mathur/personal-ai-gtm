// The browser only ever talks to the dashboard's own server; /api/gtm adds the API token there.
export const API_URL = "/api/gtm";

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

export interface RunOutcome {
  outcome: "succeeded" | "no_matching_records" | "partial" | "failed" | "cancelled";
  detail: string;
  sources_ran: number;
  sources_failed: string[];
  sources_skipped: string[];
  retrieved: number;
  new_records: number;
  pursuable_after_triage: number;
}

export interface Run {
  id: string;
  kind: string;
  status: string;
  outcome?: string | null;
  trigger?: string | null;
  attempts: number;
  error: string | null;
  created_at: string;
  state: { completed_nodes?: string[]; errors?: string[]; connector_stats?: ConnectorStat[]; outcome?: RunOutcome };
}

/** Truthful run label: "completed" never implies that anything useful was found. */
export function outcomeLabel(r: Run): string {
  if (r.status === "queued") return "Queued";
  if (r.status === "running") return "Running";
  switch (r.outcome) {
    case "succeeded": return "Succeeded — records retrieved";
    case "no_matching_records": return "Succeeded — no matching records";
    case "partial": return "Partially succeeded — some sources failed";
    case "failed": return "Failed";
    case "cancelled": return "Cancelled";
    default: return r.status === "failed" ? "Failed" : r.status === "cancelled" ? "Cancelled" : "Finished (outcome not recorded)";
  }
}

/** The discovery button names what will really run: it must never say "mock" when live sources are on. */
export function discoveryButtonLabel(connectors: { name: string; enabled: boolean; is_mock: boolean; usable: boolean; paid: boolean }[]): string {
  const on = connectors.filter((c) => c.enabled);
  const live = on.filter((c) => !c.is_mock && c.usable);
  const paid = live.filter((c) => c.paid);
  if (live.length === 0) return on.length ? "Run discovery (mock fixtures only — no live source enabled)" : "Run discovery (no source enabled)";
  return `Run discovery (${live.length} live source${live.length === 1 ? "" : "s"}${paid.length ? `, ${paid.length} PAID` : ", free"})`;
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
  triage: "unassessed" | "actionable" | "needs_verification" | "rejected";
  technical_relevance: string;
  rejection_reason: string | null;
  source_status: string;
  deadline: string | null;
  contact_route: string | null;
  missing: string[];
  reasons: string[];
  stage_a: Record<string, unknown>;
  stage_b: Record<string, { result: string; detail: string }>;
  view: RecordView;
  evidence_excerpt: string | null;
  channel: "agency" | "direct_buyer" | "proactive_buyer";
  project_type: string;
  budget_value: number | null;
  budget_currency: string | null;
  age_days: number | null;
  routes: { kind: string; value: string; role: string | null; source_url: string }[];
  ai_next_action: string | null;
  small_team_fit: string | null;
  external_delivery: string | null;
  pipeline_stage: string;
  origin: "auto" | "manual";
  intake: {
    by: string; at: string; platform: string; channel: string; date_supplied: boolean;
    delivery_suitability: string | null; reviewer_verification: string;
  } | null;
  commercial_class: string;
}

export interface Deal {
  id: string;
  opportunity_id: string;
  opportunity_title: string | null;
  stage: string;
  value_usd: number | null;
  notes: string | null;
  next_follow_up_at: string | null;
  updated_at: string;
  interactions: { id: string; kind: string; summary: string; occurred_at: string }[];
}

export interface IntakeInput {
  source_url: string; platform: string; channel: string; buyer_name?: string; requirement: string;
  excerpt: string; published_at?: string; status: string; budget_text?: string;
  contact_route?: string; delivery_suitability?: string;
}

export interface RouteRow {
  kind: string; value: string; role: string | null; source_url: string; opportunity_id: string;
  opportunity: string; organization: string | null; country: string | null; channel: string;
  triage: string; view: string; next_action: string; is_mock: boolean;
}

export interface SourcePerformance {
  sources: {
    source: string; is_mock: boolean; records: number; actionable: number; needs_verification: number;
    rejected: number; false_positives: number; expired_or_awarded: number;
    missing_contact_route: number; cost_usd: number; retrieved_total: number; duplicates_total: number;
    runs: number; last_error: string | null; demand_signals: number; usable_opportunities: number;
    cost_per_usable_usd: number | null; median_age_days: number | null;
    channels: { agency: number; direct_buyer: number };
  }[];
  candidates: Record<string, Record<string, number>>;
}

export interface Filters {
  country: string; projectType: string; channel: string; triage: string;
  minBudget: string; maxBudget: string; maxAgeDays: string;
}
export const NO_FILTERS: Filters = {
  country: "", projectType: "", channel: "", triage: "", minBudget: "", maxBudget: "", maxAgeDays: "",
};

/** Client-side filters. A budget filter excludes records whose budget is unknown (unknown != 0). */
export function applyFilters(rows: BoardRow[], f: Filters): BoardRow[] {
  const min = f.minBudget === "" ? null : Number(f.minBudget);
  const max = f.maxBudget === "" ? null : Number(f.maxBudget);
  const age = f.maxAgeDays === "" ? null : Number(f.maxAgeDays);
  return rows.filter((r) => {
    if (f.country && !(r.country ?? "").toLowerCase().includes(f.country.toLowerCase())) return false;
    if (f.projectType && r.project_type !== f.projectType) return false;
    if (f.channel && r.channel !== f.channel) return false;
    if (f.triage && r.triage !== f.triage) return false;
    if ((min !== null || max !== null) && r.budget_value === null) return false;
    if (min !== null && r.budget_value !== null && r.budget_value < min) return false;
    if (max !== null && r.budget_value !== null && r.budget_value > max) return false;
    if (age !== null && (r.age_days === null || r.age_days > age)) return false;
    return true;
  });
}

export type RecordView =
  | "active_project" | "agency_seeking" | "needs_verification" | "verified_partnership"
  | "potential_partner" | "rejected" | "unqualified_signal" | "proactive_prospect";

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

export const TOTAL_NODES = 11;

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
  cached: number;
  failed: number;
  by_task: { task: string; outcome: string; calls: number; cost_usd: number }[];
  by_agent: {
    agent: string; calls: number; cached: number; failed: number; rejected: number;
    input_tokens: number; output_tokens: number; cost_usd: number; reasons: Record<string, number>;
  }[];
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
    headers: { "content-type": "application/json", "x-gtm-action": "1", ...init?.headers },
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
  routes: () => request<RouteRow[]>("/routes"),
  fit: (id: string) => request<Fit>(`/opportunities/${id}/fit`),
  createDraft: (id: string) => request<{ draft_id: string }>(`/opportunities/${id}/draft`, { method: "POST" }),
  createProposal: (id: string) => request<ProposalRow>(`/opportunities/${id}/proposal`, { method: "POST" }),
  proposals: () => request<ProposalRow[]>("/proposals"),
  pipeline: () => request<PipelineSummary>("/pipeline"),
  intake: (body: IntakeInput) =>
    request<BoardRow>("/intake", { method: "POST", body: JSON.stringify(body) }),
  sources: () => request<SourcePerformance>("/sources/performance"),
  costs: () => request<CostSummary>("/costs"),
  cancelRun: (id: string) => request<Run>(`/runs/${id}/cancel`, { method: "POST" }),
  opportunity: (id: string) => request<OpportunityDetail>(`/opportunities/${id}`),
  analyses: (id: string) => request<Analysis[]>(`/opportunities/${id}/analyses`),
  profiles: () => request<Profile[]>("/search-profiles"),
  deals: (followUps = false) =>
    request<Deal[]>(`/deals${followUps ? "?follow_ups=true" : ""}`),
  createDeal: (opportunity_id: string) =>
    request<Deal>("/deals", { method: "POST", body: JSON.stringify({ opportunity_id }) }),
  updateDeal: (id: string, body: Partial<{ stage: string; value_usd: number; notes: string; next_follow_up_at: string; clear_follow_up: boolean }>) =>
    request<Deal>(`/deals/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  addInteraction: (dealId: string, kind: string, summary: string) =>
    request<Deal>(`/deals/${dealId}/interactions`, {
      method: "POST", body: JSON.stringify({ kind, summary }),
    }),
  markDraftSent: (id: string) =>
    request<Draft>(`/drafts/${id}/mark-sent`, { method: "POST" }),
  whoami: () => request<{ role: "admin" | "operator" | null }>("/whoami"),
  verify: (id: string, attest: string[], note?: string) =>
    request<Opportunity>(`/opportunities/${id}/verify`, { method: "POST", body: JSON.stringify({ attest, note }) }),
  allDrafts: () => request<Draft[]>("/drafts"),
  editDraft: (id: string, body: { subject?: string; body: string }) =>
    request<Draft>(`/drafts/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  approveDraft: (id: string) => request<Draft>(`/drafts/${id}/approve`, { method: "POST" }),
  rejectDraft: (id: string) => request<Draft>(`/drafts/${id}/reject`, { method: "POST" }),
  editProposal: (id: string, body: { body?: string; price_text?: string }) =>
    request<ProposalRow>(`/proposals/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  approveProposal: (id: string) => request<ProposalRow>(`/proposals/${id}/approve`, { method: "POST" }),
  proposalSent: (id: string) => request<ProposalRow>(`/proposals/${id}/mark-sent`, { method: "POST" }),
  onboarding: (dealId: string) => request<OnboardingItem[]>(`/deals/${dealId}/onboarding`),
  startOnboarding: (dealId: string) => request<OnboardingItem[]>(`/deals/${dealId}/onboarding`, { method: "POST" }),
  tickOnboarding: (id: string, done: boolean) =>
    request<OnboardingItem>(`/onboarding/${id}`, { method: "PATCH", body: JSON.stringify({ done }) }),
  schedules: () => request<SchedulesResponse>("/schedules"),
  patchSchedule: (connector: string, body: Partial<{ enabled: boolean; interval_minutes: number }>) =>
    request<ScheduleRow>(`/schedules/${connector}`, { method: "PATCH", body: JSON.stringify(body) }),
  reviewQueue: () => request<ReviewItem[]>("/review-queue"),
  queryPerformance: () =>
    request<{ queries: { query: string; channel: string; useful: number; cost_usd: number }[] }>("/queries/performance"),
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

/** Dashboard lists. Mock fixtures are removed first; each real record is in exactly one list. */
export function viewRows(rows: BoardRow[], view: RecordView): BoardRow[] {
  return splitReal(rows).real.filter((r) => r.view === view && r.origin !== "manual");
}

export interface Fit {
  organization: string | null; country: string | null; commercial_class_label: string;
  channel: string; why_relevant: string; problem: string; buying_intent: string;
  evidence: { url: string; published: string | null; quote: string; provider: string }[];
  matched_capabilities: { label: string; matched_terms: string[]; evidence_status: string }[];
  what_i_could_deliver: string[]; decision_roles: { route: string; role_as_printed: string | null }[];
  contact_routes: { kind: string; value: string; role: string | null }[];
  verdict: string; verdict_reasons: string[]; missing: string[]; confidence: string; pipeline_stage: string;
  approach: { angle: string; proposed_engagement: string; next_action: string; cautions: string[] };
}
export interface ProposalRow {
  id: string; opportunity_id: string; title: string; status: string; body: string;
  price_text: string | null; prepared_by: string; approved_by: string | null; has_placeholders: boolean;
}
export interface PipelineSummary { stages: string[]; counts: Record<string, number>; active: { opportunity_id: string; title: string; stage: string; channel: string }[] }

export const STAGE_LABEL: Record<string, string> = {
  discovered: "Discovered", researched: "Researched", qualified: "Qualified",
  outreach_prepared: "Outreach prepared", approved: "Approved", contacted: "Contacted",
  responded: "Responded", meeting: "Meeting", proposal: "Proposal", negotiation: "Negotiation",
  won: "Won", onboarding: "Onboarding", lost: "Lost", rejected: "Rejected",
};

export interface OnboardingItem { id: string; label: string; done: boolean; done_by: string | null; done_at: string | null }
export interface ScheduleRow {
  connector: string; channel: string; enabled: boolean; paid: boolean; interval_minutes: number;
  next_run_at: string | null; last_success_at: string | null; last_status: string | null;
  last_error: string | null; consecutive_failures: number; last_retrieved: number; last_new: number;
  freshness: "ok" | "stale" | "never_succeeded" | "failing" | "disabled";
}
export interface SchedulesResponse { scheduler_enabled: boolean; paid_scheduling_allowed: boolean; sources: ScheduleRow[] }
export interface ReviewItem {
  opportunity_id: string; title: string; channel: string; pursuability: string; commercial_class: string;
  first_seen_at: string; missing: string[]; next_action: string | null; sources: string[];
}
