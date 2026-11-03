import { describe, expect, it } from "vitest";
import { groupByCategory, jobTotals, runProgress, splitReal, viewRows, applyFilters, NO_FILTERS, type BoardRow, type Opportunity, type Run } from "./api";

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
