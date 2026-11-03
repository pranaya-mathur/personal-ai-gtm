"use client";

import { useCallback, useEffect, useState } from "react";
import {
  api, CATEGORY_LABEL, groupByCategory,
  type Category, type Draft, type Metrics, type Opportunity, type Run,
} from "@/lib/api";

export default function Dashboard() {
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [opps, setOpps] = useState<Opportunity[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  const load = useCallback(async () => {
    try {
      const [m, o, r, d] = await Promise.all([api.metrics(), api.opportunities(), api.runs(), api.drafts()]);
      setMetrics(m); setOpps(o); setRuns(r); setDrafts(d); setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "API unreachable");
    }
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), 5000);
    return () => clearInterval(t);
  }, [load]);

  const grouped = groupByCategory(opps);

  return (
    <main>
      <header>
        <div>
          <h1>Personal AI GTM Command Center</h1>
          <span className="muted">Milestone 1 — mock providers, no outreach is ever sent</span>
        </div>
        <button
          disabled={starting}
          onClick={async () => {
            setStarting(true);
            try { await api.startRun(); await load(); } finally { setStarting(false); }
          }}
        >
          {starting ? "Queuing…" : "Run discovery (mock)"}
        </button>
      </header>

      {error && <p className="err">Cannot reach API: {error}</p>}

      <section className="grid stats">
        <div className="card stat"><span className="muted">Pending approvals</span><b>{metrics?.drafts_pending_approval ?? "–"}</b></div>
        <div className="card stat"><span className="muted">Provider spend (USD)</span><b>{metrics ? metrics.total_provider_spend_usd.toFixed(2) : "–"}</b></div>
        <div className="card stat"><span className="muted">Opportunities</span><b>{opps.length}</b></div>
        <div className="card stat"><span className="muted">Recent runs</span><b>{runs.length}</b></div>
      </section>

      <section className="grid cols3">
        {(Object.keys(CATEGORY_LABEL) as Category[]).map((c) => (
          <div className="card" key={c}>
            <h2>{CATEGORY_LABEL[c]} ({grouped[c].length})</h2>
            {grouped[c].length === 0 && <span className="muted">None yet</span>}
            {grouped[c].map((o) => (
              <div className="item" key={o.id}>
                {o.title}
                <div>
                  <span className="badge">{o.status}</span>
                  <span className="badge">{o.verification_status}</span>
                  <span className="muted">conf {(o.confidence * 100).toFixed(0)}%</span>
                </div>
              </div>
            ))}
          </div>
        ))}
      </section>

      <section className="grid cols3">
        <div className="card">
          <h2>Outreach awaiting human approval</h2>
          {drafts.length === 0 && <span className="muted">No drafts</span>}
          {drafts.map((d) => (
            <div className="item" key={d.id}>{d.subject ?? "(no subject)"}</div>
          ))}
          <p className="muted">Approval happens via the API; editing UI arrives in Milestone 2.</p>
        </div>
        <div className="card">
          <h2>Workflow runs</h2>
          {runs.slice(0, 6).map((r) => (
            <div className="item" key={r.id}>
              <span className="badge">{r.status}</span>{r.kind}
              <div className="muted">{new Date(r.created_at).toLocaleString()}{r.error ? ` · ${r.error}` : ""}</div>
            </div>
          ))}
        </div>
      </section>
    </main>
  );
}
