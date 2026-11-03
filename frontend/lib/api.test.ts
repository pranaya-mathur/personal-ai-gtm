import { describe, expect, it } from "vitest";
import { groupByCategory, jobTotals, runProgress, splitReal, type BoardRow, type Opportunity, type Run } from "./api";

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
    expect(runProgress(run(["a", "b", "c"]))).toBe(38);
    expect(runProgress(run(Array(8).fill("x")))).toBe(100);
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
