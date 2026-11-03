"use client";

import { useCallback, useEffect, useState } from "react";
import {
  api, CATEGORY_LABEL, groupByCategory,
  runProgress, type Category, type Connector, type Draft, type Evidence, type Metrics,
  type Opportunity, type Profile, type Run,
} from "@/lib/api";

export default function Dashboard() {
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [opps, setOpps] = useState<Opportunity[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  const load = useCallback(async () => {
    try {
      const [m, o, r, d, e, c, p] = await Promise.all([
        api.metrics(), api.opportunities(), api.runs(), api.drafts(),
        api.evidence(), api.connectors(), api.profiles(),
      ]);
      setMetrics(m); setOpps(o); setRuns(r); setDrafts(d);
      setEvidence(e); setConnectors(c); setProfiles(p); setError(null);
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
          <span className="muted">Milestone 2 — mock by default; paid Apify runs need explicit approval; no outreach is ever sent</span>
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
              <progress value={runProgress(r)} max={100} style={{ width: "100%" }} />
              <div className="muted">
                {new Date(r.created_at).toLocaleString()} · attempt {r.attempts}
                {r.error ? ` · ${r.error}` : ""}
                {(r.state.errors ?? []).map((e) => <div className="err" key={e}>{e}</div>)}
              </div>
            </div>
          ))}
        </div>
        <div className="card">
          <h2>Connectors</h2>
          {connectors.map((c) => (
            <div className="item" key={c.name}>
              {c.name}
              <div>
                <span className="badge">{c.enabled ? "enabled" : "off"}</span>
                <span className="badge">{c.paid ? "paid" : "free"}</span>
                {c.paid && <span className="badge">{c.paid_execution_approved ? "spend approved" : "spend NOT approved"}</span>}
                {!c.usable && <span className="badge err">blocked</span>}
              </div>
              <div className="muted">{c.notes}</div>
            </div>
          ))}
        </div>
        <div className="card">
          <h2>Search profiles</h2>
          {profiles.length === 0 && <span className="muted">None. Create one via POST /search-profiles.</span>}
          {profiles.map((p) => (
            <div className="item" key={p.id}>
              {p.name} {!p.enabled && <span className="badge">disabled</span>}
              <div className="muted">{p.keywords.join(", ") || "any keyword"} · {p.regions.join(", ") || "worldwide"}</div>
              <button disabled={!p.enabled} onClick={async () => { await api.startRun(p.id); await load(); }}>Run</button>
            </div>
          ))}
        </div>
      </section>

      <section className="card" style={{ marginTop: 16, overflowX: "auto" }}>
        <h2>Retrieved records ({evidence.length})</h2>
        <table>
          <thead>
            <tr><th>Source</th><th>Provider</th><th>Published</th><th>Conf.</th><th>Source check</th><th>Verification</th></tr>
          </thead>
          <tbody>
            {evidence.map((e) => (
              <tr key={e.id}>
                <td><a href={e.source_url} target="_blank" rel="noopener noreferrer">{e.original_text.split("\n")[0].slice(0, 80)}</a></td>
                <td>{e.provider}</td>
                <td>{e.published_at ? new Date(e.published_at).toLocaleDateString() : "undated"}</td>
                <td>{(e.confidence * 100).toFixed(0)}%</td>
                <td><span className="badge">{e.source_check_status}</span></td>
                <td><span className="badge">{e.verification_status}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </main>
  );
}
