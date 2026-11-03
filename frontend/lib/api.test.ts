import { describe, expect, it } from "vitest";
import { groupByCategory, jobTotals, runProgress, splitReal, viewRows, STAGE_LABEL, applyFilters, NO_FILTERS, type BoardRow, type Opportunity, type Run } from "./api";

const o = (id: string, category: Opportunity["category"]): Opportunity => ({
  id, category, title: id, status: "new", verification_status: "unverified",
  confidence: 0.5, last_seen_at: "2026-01-01T00:00:00Z",
});

describe("groupByCategory", () => {
  it("keeps the three evidence classes strictly separate", () => {
    const g = groupByCategory([o("a", "confirmed_demand"), o("b", "speculative_prospect")]);
    expect(g.confirmed_demand.map((x) => x.id)).toEqual(["a"]);
    expect(g.speculative_prospect.map((x) => x.id)).toEqual(["b"]);
    expect(g.verified_partnership).toEqual([]);
  });
});

describe("runProgress", () => {
  const run = (nodes: string[]): Run => ({
    id: "r", kind: "k", status: "running", attempts: 1, error: null,
    created_at: "2026-01-01T00:00:00Z", state: { completed_nodes: nodes },
  });
  it("reports percent of workflow nodes completed", () => {
    expect(runProgress(run([]))).toBe(0);
    expect(runProgress(run(["a", "b", "c"]))).toBe(27);
    expect(runProgress(run(Array(11).fill("x")))).toBe(100);
  });
});

describe("splitReal", () => {
  it("never counts mock fixtures as real leads", () => {
    const row = (id: string, is_mock: boolean) => ({ id, is_mock }) as BoardRow;
    const { real, mock } = splitReal([row("a", true), row("b", false)]);
    expect(real.map((r) => r.id)).toEqual(["b"]);
    expect(mock.map((r) => r.id)).toEqual(["a"]);
  });
});

describe("jobTotals", () => {
  it("sums per-connector stats and tolerates runs without stats", () => {
    const stat = (n: number) => ({ connector: "c", is_mock: false, status: "succeeded", retrieved: n, rejected: 1, created: n, duplicates: 2, errors: 0, cost_usd: 0.01 });
    const run = { state: { connector_stats: [stat(3), stat(4)] } } as unknown as Run;
    expect(jobTotals(run)).toEqual({ retrieved: 7, rejected: 2, duplicates: 4, errors: 0, cost_usd: 0.02 });
    expect(jobTotals({ state: {} } as unknown as Run).retrieved).toBe(0);
  });
});

describe("viewRows", () => {
  it("puts each real record in one list and never counts speculative or mock as active projects", () => {
    const r = (id: string, view: string, is_mock = false) => ({ id, view, is_mock }) as BoardRow;
    const rows = [r("a", "active_project"), r("m", "active_project", true), r("s", "unqualified_signal"), r("x", "rejected")];
    expect(viewRows(rows, "active_project").map((x) => x.id)).toEqual(["a"]);
    expect(viewRows(rows, "rejected").map((x) => x.id)).toEqual(["x"]);
    expect(viewRows(rows, "potential_partner")).toEqual([]);
  });
  it("has 11 workflow nodes for progress", () => {
    const run = { state: { completed_nodes: new Array(11).fill("n") } } as unknown as Run;
    expect(runProgress(run)).toBe(100);
  });
});

describe("applyFilters", () => {
  const row = (o: Partial<BoardRow>) => ({ country: "GBR", project_type: "chatbot_support", channel: "direct_buyer", triage: "needs_verification", budget_value: null, age_days: 5, ...o }) as BoardRow;
  it("treats an unknown budget as unknown, never as zero or in-range", () => {
    const rows = [row({ budget_value: 8000 }), row({ budget_value: null }), row({ budget_value: 90000 })];
    expect(applyFilters(rows, { ...NO_FILTERS, minBudget: "3000", maxBudget: "10000" }).length).toBe(1);
    expect(applyFilters(rows, NO_FILTERS).length).toBe(3);
  });
  it("filters by country, channel, status, type and recency", () => {
    const rows = [row({}), row({ country: "Germany", channel: "agency", age_days: 200, project_type: "ai_agents", triage: "actionable" })];
    expect(applyFilters(rows, { ...NO_FILTERS, country: "ger" }).length).toBe(1);
    expect(applyFilters(rows, { ...NO_FILTERS, channel: "agency", triage: "actionable" }).length).toBe(1);
    expect(applyFilters(rows, { ...NO_FILTERS, maxAgeDays: "30" }).length).toBe(1);
    expect(applyFilters(rows, { ...NO_FILTERS, projectType: "ai_agents" })[0].country).toBe("Germany");
  });
});

describe("manual intake separation", () => {
  it("keeps human-supplied records out of the automatic lead lists", () => {
    const r = (id: string, origin: string) => ({ id, origin, view: "needs_verification", is_mock: false }) as BoardRow;
    expect(viewRows([r("a", "auto"), r("m", "manual")], "needs_verification").map((x) => x.id)).toEqual(["a"]);
  });
});

describe("pipeline stages", () => {
  it("labels every stage the owner defined", () => {
    for (const s of ["discovered", "researched", "qualified", "outreach_prepared", "approved", "contacted", "responded", "meeting", "proposal", "negotiation", "won", "onboarding", "lost"]) {
      expect(STAGE_LABEL[s]).toBeTruthy();
    }
  });
  it("keeps proactive triggers out of the active-project list", () => {
    const r = (id: string, view: string) => ({ id, view, origin: "auto", is_mock: false }) as BoardRow;
    expect(viewRows([r("t", "proactive_prospect"), r("a", "active_project")], "active_project").map((x) => x.id)).toEqual(["a"]);
  });
});

import { discoveryButtonLabel, outcomeLabel } from "./api";
import { pickToken, sameOrigin } from "./proxyPolicy";

describe("discoveryButtonLabel", () => {
  const c = (name: string, o: Partial<{ enabled: boolean; is_mock: boolean; usable: boolean; paid: boolean }> = {}) =>
    ({ name, enabled: true, is_mock: false, usable: true, paid: false, ...o });
  it("never says mock when live sources would run", () => {
    expect(discoveryButtonLabel([c("ted_api"), c("canadabuys")])).toBe("Run discovery (2 live sources, free)");
    expect(discoveryButtonLabel([c("ted_api"), c("apify_x", { paid: true })])).toContain("1 PAID");
  });
  it("says mock only when only fixtures are enabled", () => {
    expect(discoveryButtonLabel([c("mock_opportunities", { is_mock: true })])).toContain("mock fixtures only");
    expect(discoveryButtonLabel([])).toContain("no source enabled");
  });
});

describe("outcomeLabel", () => {
  const run = (o: Partial<Run>): Run => ({ id: "r", kind: "k", status: "succeeded", attempts: 1, error: null, created_at: "", state: {}, ...o });
  it("does not call a no-record or failed run a success", () => {
    expect(outcomeLabel(run({ outcome: "no_matching_records" }))).toContain("no matching records");
    expect(outcomeLabel(run({ status: "failed", outcome: "failed" }))).toBe("Failed");
    expect(outcomeLabel(run({ outcome: "partial" }))).toContain("Partially");
  });
});

describe("proxy policy", () => {
  const env = { GTM_API_TOKEN: "op", GTM_ADMIN_API_TOKEN: "adm" };
  const id = "11111111-1111-1111-1111-111111111111";
  it("uses the admin token only for approvals and never for operator actions", () => {
    expect(pickToken("POST", `proposals/${id}/approve`, env, "admin").token).toBe("adm");
    expect(pickToken("POST", `drafts/${id}/approve`, env, "admin").token).toBe("adm");
    expect(pickToken("POST", `opportunities/${id}/verify`, env).token).toBe("op");
    expect(pickToken("PATCH", `proposals/${id}`, env).token).toBe("op");
  });
  it("refuses approvals when no admin token is configured, and unknown actions", () => {
    expect(pickToken("POST", `drafts/${id}/approve`, { GTM_API_TOKEN: "op" }, "admin").forbidden).toContain("not configured");
    expect(pickToken("POST", "capabilities", env).forbidden).toBeTruthy();
    expect(pickToken("PATCH", "schedules/ted_api", env, "admin").token).toBe("adm");
  });
  it("rejects cross-origin mutating requests", () => {
    const h = (m: Record<string, string>) => ({ headers: { get: (n: string) => m[n] ?? null } });
    expect(sameOrigin(h({ origin: "https://evil.example", host: "dash.local" }))).toBe(false);
    expect(sameOrigin(h({ origin: "http://dash.local", host: "dash.local" }))).toBe(true);
    expect(sameOrigin(h({}))).toBe(false);
    expect(sameOrigin(h({ "x-gtm-action": "1" }))).toBe(true);
  });
});
