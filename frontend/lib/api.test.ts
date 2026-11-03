import { describe, expect, it } from "vitest";
import { groupByCategory, runProgress, type Opportunity, type Run } from "./api";

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
