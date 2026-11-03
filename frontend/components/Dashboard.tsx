"use client";

import { useCallback, useEffect, useState } from "react";
import {
  api, splitReal,
  runProgress, type AiStatus, type AiUsage, type Connector, type Draft, type Evidence, type Metrics,
  type BoardRow, type CostSummary, type Filters, NO_FILTERS, applyFilters, type Profile, type Run, type RouteRow, type SourcePerformance,
} from "@/lib/api";
import { ContactsView, CostPanel, FilterBar, OperationsView, RecordsView, ResearchDetail, SourcesView, SpendView } from "./Views";

type Tab =
  | "overview" | "projects" | "agencies" | "partners" | "verify" | "contacts"
  | "sources" | "spend" | "rejected" | "operations" | "research";
const TABS: [Tab, string][] = [
  ["overview", "Overview"], ["projects", "Active AI implementation opportunities"],
  ["agencies", "Agencies seeking delivery partners"], ["partners", "Verified & potential agency partners"],
  ["verify", "Relevant, needs verification"], ["contacts", "Contact routes & next actions"],
  ["sources", "Discovery sources"], ["spend", "OpenAI & provider spend"],
  ["rejected", "Rejected / unqualified"], ["operations", "Discovery operations"], ["research", "Research detail"],
];
const FILTERED: Tab[] = ["projects", "agencies", "partners", "verify", "rejected"];

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
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [routes, setRoutes] = useState<RouteRow[]>([]);
  const [sources, setSources] = useState<SourcePerformance | null>(null);

  const load = useCallback(async () => {
    try {
      const [m, o, r, d, e, c, p, ai, au, b, co, ro, so] = await Promise.all([
        api.metrics(), api.opportunities(), api.runs(), api.drafts(),
        api.evidence(), api.connectors(), api.profiles(), api.aiStatus(), api.aiUsage(),
        api.board(), api.costs(), api.routes(), api.sources(),
      ]);
      setBoard(b); setCosts(co); setRoutes(ro); setSources(so); setLoaded(true);
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

  const shown = applyFilters(board, filters);
  const open = (id: string) => { setSelected(id); setTab("research"); };
  const { real } = splitReal(board);
  const realCount = real.length;
  const count = (v: BoardRow["view"]) => real.filter((r) => r.view === v).length;
  const confirmed = count("active_project");
  const agenciesSeeking = count("agency_seeking");
  const partners = count("verified_partnership");
  const pending = count("needs_verification");

  return (
    <main>
      <header>
        <div>
          <h1>Personal AI GTM Command Center</h1>
          <span className="muted">Development stage — mock by default; paid runs need explicit approval; nothing is ever sent</span>
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

      {FILTERED.includes(tab) && <FilterBar f={filters} set={setFilters} />}
      {tab === "projects" && <RecordsView rows={shown} view="active_project" onOpen={open}
        title="Active AI implementation opportunities"
        blurb="Open, relevant requests from direct buyers with a usable contact route. Eligibility items may still be unverified; nothing is human-verified until you verify it." />}
      {tab === "agencies" && <RecordsView rows={shown} view="agency_seeking" onOpen={open}
        title="Agencies seeking delivery partners"
        blurb="Agency posts that state a need for external AI delivery capacity. Each must be confirmed as current by a person; a page or post alone is not proof the need is still open." />}
      {tab === "partners" && <RecordsView rows={shown} view={["verified_partnership", "potential_partner"]} onOpen={open}
        title="Verified existing models and potential agency partners"
        blurb="Verified = the source evidences a subcontracting / white-label model (no current project implied). Potential = the agency exists; no evidence of demand." />}
      {tab === "verify" && <RecordsView rows={shown} view="needs_verification" onOpen={open}
        title="Technically relevant, requiring verification"
        blurb="Plausibly AI-related, but something is unconfirmed (open status, scope, contact route). Not counted as active projects." />}
      {tab === "rejected" && <RecordsView rows={shown} view={["rejected", "unqualified_signal"]} onOpen={open}
        title="Rejected and unqualified records"
        blurb="Kept for audit with the reason (not relevant, closed, awarded, or a signal that states no need). Evidence is preserved." />}
      {tab === "contacts" && <ContactsView routes={routes} onOpen={open} />}
      {tab === "sources" && <SourcesView data={sources} />}
      {tab === "spend" && <SpendView costs={costs} usage={aiUsage} />}
      {tab === "operations" && (
        <OperationsView runs={runs} onCancel={async (id) => { await api.cancelRun(id); await load(); }} />
      )}
      {tab === "research" && <ResearchDetail id={selected} row={board.find((r) => r.id === selected)} />}

      {tab === "overview" && <>
      <section className="grid stats">
        <div className="card stat"><span className="muted">Real opportunities</span><b>{realCount}</b></div>
        <div className="card stat"><span className="muted">Confirmed active AI projects</span><b>{confirmed}</b></div>
        <div className="card stat"><span className="muted">Agencies seeking partners (unconfirmed)</span><b>{agenciesSeeking}</b></div>
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
          <li>Active AI implementation opportunities: {count("active_project")}</li>
          <li>Agencies seeking delivery partners: {count("agency_seeking")}</li>
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
          <p className="muted">Approval is an admin-only API action recorded under the approver's name; the draft editor is not built yet.</p>
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
