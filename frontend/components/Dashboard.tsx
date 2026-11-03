"use client";

import { useCallback, useEffect, useState } from "react";
import {
  api, splitReal, groupByCategory,
  runProgress, type AiStatus, type AiUsage, type Connector, type Draft, type Evidence, type Metrics,
  type BoardRow, type CostSummary, type Profile, type Run,
} from "@/lib/api";
import { CostPanel, OperationsView, RecordsView, ResearchDetail } from "./Views";

type Tab = "overview" | "active" | "verify" | "partnerships" | "potential" | "rejected" | "operations" | "research";
const TABS: [Tab, string][] = [
  ["overview", "Overview"], ["active", "Confirmed active AI projects"],
  ["verify", "Relevant, needs verification"], ["partnerships", "Verified partnerships"],
  ["potential", "Potential agency partners"], ["rejected", "Rejected / off-topic"],
  ["operations", "Discovery operations"], ["research", "Research detail"],
];

export default function Dashboard() {
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [opps, setOpps] = useState<{ id: string }[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [aiStatus, setAiStatus] = useState<AiStatus | null>(null);
  const [aiUsage, setAiUsage] = useState<AiUsage | null>(null);
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [board, setBoard] = useState<BoardRow[]>([]);
  const [costs, setCosts] = useState<CostSummary | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [selected, setSelected] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(async () => {
    try {
      const [m, o, r, d, e, c, p, ai, au, b, co] = await Promise.all([
        api.metrics(), api.opportunities(), api.runs(), api.drafts(),
        api.evidence(), api.connectors(), api.profiles(), api.aiStatus(), api.aiUsage(),
        api.board(), api.costs(),
      ]);
      setBoard(b); setCosts(co); setLoaded(true);
      setMetrics(m); setOpps(o); setRuns(r); setDrafts(d);
      setEvidence(e); setConnectors(c); setProfiles(p); setAiStatus(ai); setAiUsage(au); setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "API unreachable");
    }
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), 5000);
    return () => clearInterval(t);
  }, [load]);

  const open = (id: string) => { setSelected(id); setTab("research"); };
  const { real } = splitReal(board);
  const realCount = real.length;
  const count = (v: BoardRow["view"]) => real.filter((r) => r.view === v).length;
  const confirmed = count("active_project");
  const partners = count("verified_partnership");
  const pending = count("needs_verification");

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
      {!error && !loaded && <p className="muted">Loading…</p>}

      <nav style={{ display: "flex", gap: 8, flexWrap: "wrap", margin: "12px 0" }}>
        {TABS.map(([t, label]) => (
          <button key={t} aria-pressed={tab === t} onClick={() => setTab(t)}>{label}</button>
        ))}
      </nav>

      {tab === "active" && <RecordsView rows={board} view="active_project" onOpen={open}
        title="Confirmed active AI projects"
        blurb="Open, relevant external requirements with a buyer contact route. Eligibility items may still be unverified; nothing here is human-verified until you verify it." />}
      {tab === "verify" && <RecordsView rows={board} view="needs_verification" onOpen={open}
        title="Technically relevant, requiring verification"
        blurb="Plausibly AI-related, but something is unconfirmed (open status, scope, contact route). Not counted as active projects." />}
      {tab === "partnerships" && <RecordsView rows={board} view="verified_partnership" onOpen={open}
        title="Verified delivery partnerships"
        blurb="Sources evidencing a subcontracting / white-label / partner delivery model. No current project is implied." />}
      {tab === "potential" && <RecordsView rows={board} view="potential_partner" onOpen={open}
        title="Potential agency partners"
        blurb="Agencies that exist and offer complementary services. No evidence of demand or external delivery: do not assume a need." />}
      {tab === "rejected" && <RecordsView rows={board} view="rejected" onOpen={open}
        title="Rejected / off-topic"
        blurb="Kept for audit with the reason (not relevant, closed, awarded). Evidence is preserved." />}
      {tab === "operations" && (
        <OperationsView runs={runs} onCancel={async (id) => { await api.cancelRun(id); await load(); }} />
      )}
      {tab === "research" && <ResearchDetail id={selected} row={board.find((r) => r.id === selected)} />}

      {tab === "overview" && <>
      <section className="grid stats">
        <div className="card stat"><span className="muted">Real opportunities</span><b>{realCount}</b></div>
        <div className="card stat"><span className="muted">Confirmed active AI projects</span><b>{confirmed}</b></div>
        <div className="card stat"><span className="muted">Verified delivery partnerships</span><b>{partners}</b></div>
        <div className="card stat"><span className="muted">Relevant, needs verification</span><b>{pending}</b></div>
      </section>
      <section className="grid cols3"><CostPanel costs={costs} /></section>
      {board.length > 0 && realCount === 0 && (
        <p className="muted">All {board.length} records are fictional mock fixtures; no real leads have been discovered yet.</p>
      )}

      <section className="grid stats">
        <div className="card stat"><span className="muted">Pending approvals</span><b>{metrics?.drafts_pending_approval ?? "–"}</b></div>
        <div className="card stat"><span className="muted">Provider spend (USD)</span><b>{metrics ? metrics.total_provider_spend_usd.toFixed(2) : "–"}</b></div>
        <div className="card stat"><span className="muted">All records (incl. mock)</span><b>{opps.length}</b></div>
        <div className="card stat"><span className="muted">Recent runs</span><b>{runs.length}</b></div>
      </section>

      {splitReal(board).mock.length > 0 && (
        <section className="card">
          <h2>Mock fixtures — fictional, not leads ({splitReal(board).mock.length})</h2>
          {splitReal(board).mock.map((m) => (
            <div className="item" key={m.id}><span className="badge err">MOCK</span>{m.title}</div>
          ))}
        </section>
      )}

      <section className="card">
        <h2>Where records stand (real records only)</h2>
        <ul>
          <li>Confirmed active AI projects: {count("active_project")}</li>
          <li>Relevant, needs verification: {count("needs_verification")}</li>
          <li>Verified delivery partnerships: {count("verified_partnership")}</li>
          <li>Potential agency partners: {count("potential_partner")}</li>
          <li>Rejected / off-topic: {count("rejected")}</li>
          <li>Unqualified signals (not projects): {count("unqualified_signal")}</li>
        </ul>
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
          <h2>AI ({aiStatus?.provider ?? "–"})</h2>
          <div>
            <span className="badge">{aiStatus?.mode ?? "–"} mode</span>
            <span className="badge">{aiStatus?.would_make_paid_calls ? "PAID CALLS ACTIVE" : "no paid calls"}</span>
          </div>
          <div className="muted">
            {aiUsage ? `${aiUsage.calls} model calls (${aiUsage.cached} cache hits, ${aiUsage.failed} refused) · ${aiUsage.input_tokens} in / ${aiUsage.output_tokens} out tokens · $${aiUsage.cost_usd.toFixed(4)}` : "–"}
          </div>
          {aiStatus && !aiStatus.would_make_paid_calls && (
            <div className="muted">
              Live needs: mock off, paid enabled{aiStatus.api_key_configured ? "" : ", API key"}{aiStatus.model ? "" : ", model"}{aiStatus.prices_configured ? "" : ", prices"}.
            </div>
          )}
          {(aiUsage?.by_agent ?? []).map((a) => (
            <div className="item" key={a.agent}>
              {a.agent}: {a.calls} calls · {a.rejected} rejected · {a.cached} cached · ${a.cost_usd.toFixed(4)}
              {Object.entries(a.reasons).map(([why, n]) => <div className="muted" key={why}>{n}× {why}</div>)}
            </div>
          ))}
          {(aiUsage?.by_task ?? []).map((t) => (
            <div className="item" key={t.task + t.outcome}>{t.task} <span className="badge">{t.outcome}</span> <span className="muted">{t.calls}×</span></div>
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
      </>}
    </main>
  );
}
