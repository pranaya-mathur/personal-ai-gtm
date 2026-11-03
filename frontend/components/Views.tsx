"use client";

import { useEffect, useState } from "react";
import {
  api, jobTotals, splitReal, STAGE_LABEL, viewRows,
  type AiUsage, type Analysis, type BoardRow, type CostSummary, type Deal, type Filters, NO_FILTERS, type RouteRow, type SourcePerformance, type OpportunityDetail, type RecordView, type Run,
} from "@/lib/api";

const MOCK_BADGE = <span className="badge err" title="Fictional fixture, not a real lead">MOCK FIXTURE</span>;

function Empty({ what }: { what: string }) {
  return <p className="muted">No {what} yet. Run discovery, or enable a real connector (needs approval).</p>;
}

function MockNotice({ n }: { n: number }) {
  if (n === 0) return null;
  return <p className="muted">{n} mock fixture record(s) hidden from the lead lists. They are fictional, not leads.</p>;
}

export function CostPanel({ costs }: { costs: CostSummary | null }) {
  if (!costs) return <div className="card"><h2>Costs</h2><span className="muted">Loading…</span></div>;
  return (
    <div className="card">
      <h2>Costs (estimated)</h2>
      {costs.lines.map((l) => (
        <div className="item" key={l.family}>
          {l.family}: ${l.spent_usd.toFixed(4)}
          {l.cap_usd !== null && <span className="muted"> of ${l.cap_usd.toFixed(2)} cap · ${l.remaining_usd?.toFixed(4)} left</span>}
        </div>
      ))}
      <div className="item">Total ${costs.total_spent_usd.toFixed(4)}
        {costs.total_cap_usd !== null && <span className="muted"> of ${costs.total_cap_usd.toFixed(2)}</span>}
      </div>
      <div className="muted">Today ${costs.today_usd.toFixed(4)} · month ${costs.month_usd.toFixed(4)}</div>
      <p className="muted">{costs.note}</p>
    </div>
  );
}

const TRIAGE_LABEL: Record<string, string> = {
  actionable: "Actionable (still verify yourself)",
  needs_verification: "Needs verification",
  rejected: "Rejected",
  unassessed: "Not yet assessed",
};

function Stage({ r }: { r: BoardRow }) {
  return (
    <div>
      <span className="badge">{TRIAGE_LABEL[r.triage] ?? r.triage}</span>
      <span className="badge">tech: {r.technical_relevance}</span>
      <span className="badge">source: {r.source_status}</span>
      {r.rejection_reason && <span className="badge err">{r.rejection_reason}</span>}
      <div className="muted">
        {Object.entries(r.stage_b).map(([k, v]) => `${k}: ${v.result}`).join(" · ")}
      </div>
      {r.score !== null && <div className="muted">rank score {r.score}</div>}
    </div>
  );
}

export function RecordsView({
  rows, view, title, blurb, onOpen,
}: { rows: BoardRow[]; view: RecordView | RecordView[]; title: string; blurb: string; onOpen: (id: string) => void }) {
  const views = Array.isArray(view) ? view : [view];
  const mock = splitReal(rows).mock.filter((r) => views.includes(r.view)).length;
  const list = views.flatMap((v) => viewRows(rows, v));
  return (
    <section className="card" style={{ overflowX: "auto" }}>
      <h2>{title} ({list.length})</h2>
      <p className="muted">{blurb}</p>
      <MockNotice n={mock} />
      {list.length === 0 ? <Empty what={title.toLowerCase()} /> : (
        <table>
          <thead><tr>
            <th>Organization</th><th>Country</th><th>Requirement</th><th>Original evidence</th>
            <th>Qualification</th><th>Budget</th><th>Missing information</th><th>Next action</th>
          </tr></thead>
          <tbody>
            {list.map((r) => (
              <tr key={r.id}>
                <td>{r.organization ?? "unknown"}{r.organization_kind === "agency" ? " (agency)" : ""}</td>
                <td>{r.country ?? "unknown"}</td>
                <td><a href="#" onClick={(e) => { e.preventDefault(); onOpen(r.id); }}>{r.title}</a>
                  <div className="muted">{r.channel === "agency" ? "agency channel" : r.channel === "proactive_buyer" ? "proactive prospect (trigger)" : "direct buyer"} · {r.project_type.replace("_", " ")} · {r.verification_status}{r.deadline ? ` · deadline ${new Date(r.deadline).toLocaleDateString()}` : ""}</div></td>
                <td>{r.evidence_excerpt && <blockquote>{r.evidence_excerpt}</blockquote>}
                  {r.source_url && <a href={r.source_url} target="_blank" rel="noopener noreferrer">source ({r.evidence_count})</a>}</td>
                <td><Stage r={r} /></td>
                <td>{r.stated_budget ?? "unknown"}{r.small_team_fit && <div className="muted">small-team fit (AI): {r.small_team_fit}</div>}</td>
                <td>{r.missing.length === 0 ? "–" : <ul>{r.missing.map((m) => <li key={m}>{m}</li>)}</ul>}</td>
                <td>{r.next_action}{r.ai_next_action && <div className="muted">AI suggestion (unverified): {r.ai_next_action}</div>}
                  {r.routes.length > 0 && <div className="muted">routes: {r.routes.map((x) => x.value).join(", ")}</div>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

export function OperationsView({ runs, onCancel }: { runs: Run[]; onCancel: (id: string) => void }) {
  return (
    <section className="card" style={{ overflowX: "auto" }}>
      <h2>Discovery operations</h2>
      {runs.length === 0 ? <Empty what="discovery jobs" /> : (
        <table>
          <thead><tr><th>Started</th><th>Status</th><th>Sources</th><th>Retrieved</th><th>Rejected</th><th>Duplicates</th><th>Provider errors</th><th>Actual cost</th><th /></tr></thead>
          <tbody>
            {runs.map((r) => {
              const t = jobTotals(r);
              const stats = r.state.connector_stats ?? [];
              return (
                <tr key={r.id}>
                  <td>{new Date(r.created_at).toLocaleString()}</td>
                  <td><span className="badge">{r.status}</span></td>
                  <td>{stats.length === 0 ? "–" : stats.map((s) => (
                    <div key={s.connector}>{s.connector} {s.is_mock && MOCK_BADGE} <span className="muted">{s.status}</span></div>
                  ))}</td>
                  <td>{t.retrieved}</td><td>{t.rejected}</td><td>{t.duplicates}</td>
                  <td>{t.errors}{(r.state.errors ?? []).map((e) => <div className="err" key={e}>{e}</div>)}{r.error && <div className="err">{r.error}</div>}</td>
                  <td>${t.cost_usd.toFixed(4)}</td>
                  <td>{(r.status === "queued" || r.status === "running") && <button onClick={() => onCancel(r.id)}>Cancel</button>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}

export function ResearchDetail({ id, row, onChanged }: { id: string | null; row: BoardRow | undefined; onChanged?: () => void }) {
  const [detail, setDetail] = useState<OpportunityDetail | null>(null);
  const [analyses, setAnalyses] = useState<Analysis[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    setDetail(null); setAnalyses([]); setError(null);
    Promise.all([api.opportunity(id), api.analyses(id)])
      .then(([d, a]) => { setDetail(d); setAnalyses(a); })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "failed to load"));
  }, [id]);

  if (!id) return <section className="card"><h2>Research detail</h2><span className="muted">Select a record from the Opportunities or Partnerships view.</span></section>;
  if (error) return <section className="card"><p className="err">Could not load record: {error}</p></section>;
  if (!detail) return <section className="card"><span className="muted">Loading…</span></section>;
  return (
    <>
    <FitPanel id={id} onChanged={onChanged ?? (() => undefined)} />
    <section className="grid cols3">
      <div className="card">
        <h2>{detail.title} {row?.is_mock && MOCK_BADGE}</h2>
        <div className="muted">{row?.organization ?? "unknown organization"} · {row?.country ?? "country unknown"}</div>
        <h2>Decision explanation</h2>
        <p>{row?.qualification_rationale ?? "Not yet qualified."}</p>
        {row && row.reasons.length > 0 && <ul>{row.reasons.map((x) => <li key={x}>{x}</li>)}</ul>}
        {row && Object.entries(row.stage_b).map(([k, v]) => (
          <div className="item" key={k}><span className="badge">{v.result}</span>{k}: {v.detail}</div>
        ))}
        {row && row.missing.length > 0 && <><h2>Missing information</h2><ul>{row.missing.map((m) => <li key={m}>{m}</li>)}</ul></>}
        <p><b>Next action:</b> {row?.next_action}</p>
      </div>
      <div className="card">
        <h2>Original evidence ({detail.evidence.length})</h2>
        {detail.evidence.map((e) => (
          <div className="item" key={e.id}>
            <a href={e.source_url} target="_blank" rel="noopener noreferrer">{e.source_url}</a>
            <div className="muted">{e.provider} · published {e.published_at ? new Date(e.published_at).toLocaleDateString() : "undated"} · retrieved {new Date(e.retrieved_at).toLocaleString()}</div>
            <div><span className="badge">{e.verification_status}</span><span className="badge">source: {e.source_check_status}</span>{e.is_mock && MOCK_BADGE}</div>
            <blockquote>{e.original_text}</blockquote>
          </div>
        ))}
      </div>
      <div className="card">
        <h2>AI analysis</h2>
        {analyses.length === 0 && <span className="muted">No AI analysis stored for this record.</span>}
        {analyses.map((a) => (
          <div className="item" key={a.id}>
            {a.task} <span className="badge">{a.validated ? "claims verified against evidence" : "rejected"}</span>
            <div className="muted">{a.provider}/{a.model}</div>
            {a.rejection_reason && <div className="err">{a.rejection_reason}</div>}
            {a.output && <pre>{JSON.stringify(a.output, null, 2)}</pre>}
          </div>
        ))}
      </div>
    </section>
    </>
  );
}


const TYPES = ["rag_knowledge", "ai_agents", "chatbot_support", "document_intelligence", "workflow_automation", "llm_integration", "computer_vision", "ml_predictive", "general_ai", "other"];

export function FilterBar({ f, set }: { f: Filters; set: (f: Filters) => void }) {
  const upd = (k: keyof Filters) => (e: { target: { value: string } }) => set({ ...f, [k]: e.target.value });
  return (
    <section className="card" style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "end" }}>
      <label>Country<input value={f.country} onChange={upd("country")} placeholder="any" /></label>
      <label>Project type
        <select value={f.projectType} onChange={upd("projectType")}>
          <option value="">any</option>{TYPES.map((t) => <option key={t} value={t}>{t.replace("_", " ")}</option>)}
        </select></label>
      <label>Channel
        <select value={f.channel} onChange={upd("channel")}>
          <option value="">any</option><option value="agency">agency</option><option value="direct_buyer">direct buyer</option>
        </select></label>
      <label>Qualification
        <select value={f.triage} onChange={upd("triage")}>
          <option value="">any</option><option value="actionable">actionable</option>
          <option value="needs_verification">needs verification</option><option value="rejected">rejected</option>
        </select></label>
      <label>Budget min<input value={f.minBudget} onChange={upd("minBudget")} inputMode="numeric" style={{ width: 90 }} /></label>
      <label>Budget max<input value={f.maxBudget} onChange={upd("maxBudget")} inputMode="numeric" style={{ width: 90 }} /></label>
      <label>Recency
        <select value={f.maxAgeDays} onChange={upd("maxAgeDays")}>
          <option value="">any age</option><option value="7">last 7 days</option><option value="30">last 30 days</option><option value="90">last 90 days</option>
        </select></label>
      <button onClick={() => set(NO_FILTERS)}>Clear</button>
      <span className="muted">Budget filters hide records whose budget is unknown.</span>
    </section>
  );
}

export function ContactsView({ routes, onOpen }: { routes: RouteRow[]; onOpen: (id: string) => void }) {
  const real = routes.filter((r) => !r.is_mock);
  return (
    <section className="card" style={{ overflowX: "auto" }}>
      <h2>Contact routes and next actions ({real.length})</h2>
      <p className="muted">Exactly what each source published. Nothing is guessed or purchased; nothing is sent.</p>
      {real.length === 0 ? <Empty what="published contact routes" /> : (
        <table>
          <thead><tr><th>Organization</th><th>Route</th><th>Role (as printed)</th><th>Record</th><th>Status</th><th>Next action</th><th>Source</th></tr></thead>
          <tbody>
            {real.map((r) => (
              <tr key={`${r.opportunity_id}-${r.kind}-${r.value}`}>
                <td>{r.organization ?? "unknown"}{r.country ? ` (${r.country})` : ""}</td>
                <td><span className="badge">{r.kind.replace("_", " ")}</span> {r.kind === "email" ? r.value : <a href={r.value} target="_blank" rel="noopener noreferrer">{r.value}</a>}</td>
                <td>{r.role ?? "not stated"}</td>
                <td><a href="#" onClick={(e) => { e.preventDefault(); onOpen(r.opportunity_id); }}>{r.opportunity}</a></td>
                <td><span className="badge">{r.triage}</span></td>
                <td>{r.next_action}</td>
                <td><a href={r.source_url} target="_blank" rel="noopener noreferrer">source</a></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

export function SourcesView({ data }: { data: SourcePerformance | null }) {
  if (!data) return <section className="card"><span className="muted">Loading…</span></section>;
  const origins = Object.entries(data.candidates);
  return (
    <>
      <section className="card" style={{ overflowX: "auto" }}>
        <h2>Source performance</h2>
        <p className="muted">Built from stored records only. "False positives" = retrieved but rejected as not relevant.</p>
        {data.sources.length === 0 ? <Empty what="source records" /> : (
          <table>
            <thead><tr><th>Source</th><th>Retrieved (all runs)</th><th>Stored</th><th>Demand signals</th><th>Usable (with route)</th><th>False positives</th><th>Expired / awarded</th><th>No contact route</th><th>Median age (days)</th><th>Channel A / B</th><th>Cost</th><th>Cost per usable</th><th>Last problem</th></tr></thead>
            <tbody>{data.sources.map((s) => (
              <tr key={s.source}>
                <td>{s.source} {s.is_mock && MOCK_BADGE}</td><td>{s.retrieved_total}</td><td>{s.records}</td>
                <td>{s.demand_signals}</td><td>{s.usable_opportunities}</td><td>{s.false_positives}</td>
                <td>{s.expired_or_awarded}</td><td>{s.missing_contact_route}</td>
                <td>{s.median_age_days ?? "–"}</td><td>{s.channels.agency} / {s.channels.direct_buyer}</td>
                <td>${s.cost_usd.toFixed(4)}</td>
                <td>{s.cost_per_usable_usd === null ? "n/a (0 usable)" : `$${s.cost_per_usable_usd.toFixed(4)}`}</td>
                <td>{s.last_error ?? "–"}</td>
              </tr>))}</tbody>
          </table>
        )}
      </section>
      <section className="card">
        <h2>Automatic candidate discovery</h2>
        <p className="muted">Organizations found automatically. A candidate is a lead to read, not evidence.</p>
        {origins.length === 0 ? <Empty what="discovered candidates" /> : origins.map(([o, st]) => (
          <div className="item" key={o}>{o}: {Object.entries(st).map(([k, v]) => `${k} ${v}`).join(" · ")}</div>
        ))}
      </section>
    </>
  );
}

export function SpendView({ costs, usage }: { costs: CostSummary | null; usage: AiUsage | null }) {
  return (
    <section className="grid cols3">
      <CostPanel costs={costs} />
      <div className="card">
        <h2>OpenAI usage by agent</h2>
        {!usage || usage.by_agent.length === 0 ? <span className="muted">No AI calls recorded.</span> : usage.by_agent.map((a) => (
          <div className="item" key={a.agent}>
            {a.agent}: {a.calls} calls · {a.cached} cache hits · {a.rejected} rejected · {a.input_tokens} in / {a.output_tokens} out · ${a.cost_usd.toFixed(4)}
            {Object.entries(a.reasons).map(([why, n]) => <div className="muted" key={why}>{n}× {why}</div>)}
          </div>
        ))}
      </div>
    </section>
  );
}


const EMPTY_INTAKE = {
  source_url: "", platform: "reddit", channel: "direct_buyer", buyer_name: "", requirement: "",
  excerpt: "", published_at: "", status: "unknown", budget_text: "", contact_route: "", delivery_suitability: "",
};

export function IntakeView({ rows, onSaved, onOpen }: { rows: BoardRow[]; onSaved: () => void; onOpen: (id: string) => void }) {
  const [f, setF] = useState(EMPTY_INTAKE);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof EMPTY_INTAKE) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const manual = rows.filter((r) => r.origin === "manual");
  async function submit() {
    setBusy(true); setMsg(null);
    try {
      const body = Object.fromEntries(Object.entries(f).filter(([, v]) => v !== "")) as unknown as import("@/lib/api").IntakeInput;
      if (f.published_at) body.published_at = new Date(f.published_at).toISOString();
      await api.intake(body);
      setF(EMPTY_INTAKE); setMsg("Saved. It is stored as an unverified, human-supplied record."); onSaved();
    } catch (e) { setMsg(e instanceof Error ? `Not saved: ${e.message}` : "Not saved"); } finally { setBusy(false); }
  }
  return (
    <>
      <section className="card">
        <h2>Add a lead you found (human-reviewed)</h2>
        <p className="muted">Paste only text you are permitted to copy. Nothing is fetched or inferred; leave unknown fields empty. These records stay separate from automatic discovery and start unverified.</p>
        <div style={{ display: "grid", gap: 8, maxWidth: 720 }}>
          <label>Original post URL<input value={f.source_url} onChange={set("source_url")} placeholder="https://…" /></label>
          <label>Platform
            <select value={f.platform} onChange={set("platform")}>
              {["reddit", "linkedin", "facebook", "slack", "discord", "forum", "email", "other"].map((p) => <option key={p}>{p}</option>)}
            </select></label>
          <label>Channel
            <select value={f.channel} onChange={set("channel")}><option value="direct_buyer">direct buyer</option><option value="agency">agency seeking partners</option></select></label>
          <label>Buyer / agency name (if shown)<input value={f.buyer_name} onChange={set("buyer_name")} /></label>
          <label>Requirement (one line)<input value={f.requirement} onChange={set("requirement")} /></label>
          <label>Verbatim excerpt<textarea rows={4} value={f.excerpt} onChange={set("excerpt")} /></label>
          <label>Post date (if shown)<input type="date" value={f.published_at} onChange={set("published_at")} /></label>
          <label>Still open?
            <select value={f.status} onChange={set("status")}><option value="unknown">unknown</option><option value="open">open</option><option value="closed">closed</option></select></label>
          <label>Budget as stated<input value={f.budget_text} onChange={set("budget_text")} placeholder="e.g. $4,000" /></label>
          <label>Contact / application route (email or link)<input value={f.contact_route} onChange={set("contact_route")} /></label>
          <label>Delivery suitability (your note)<input value={f.delivery_suitability} onChange={set("delivery_suitability")} /></label>
          <div><button disabled={busy} onClick={() => void submit()}>{busy ? "Saving…" : "Save lead"}</button> {msg && <span className="muted">{msg}</span>}</div>
        </div>
      </section>
      <RecordsList rows={manual} onOpen={onOpen} />
    </>
  );
}

function RecordsList({ rows, onOpen }: { rows: BoardRow[]; onOpen: (id: string) => void }) {
  return (
    <section className="card" style={{ overflowX: "auto" }}>
      <h2>Manually added leads ({rows.length})</h2>
      {rows.length === 0 ? <p className="muted">None yet.</p> : (
        <table>
          <thead><tr><th>Added by</th><th>Platform</th><th>Requirement</th><th>Status</th><th>Budget</th><th>Contact</th><th>Qualification</th><th>Next action</th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.id}>
              <td>{r.intake?.by ?? "–"}</td><td>{r.intake?.platform ?? "–"}</td>
              <td><a href="#" onClick={(e) => { e.preventDefault(); onOpen(r.id); }}>{r.title}</a>
                <div className="muted">{r.published_at ? new Date(r.published_at).toLocaleDateString() : "date not supplied"} · <span className="badge">{r.verification_status}</span></div></td>
              <td>{r.source_status}</td><td>{r.stated_budget ?? "unknown"}</td>
              <td>{r.routes.length ? r.routes.map((x) => x.value).join(", ") : "none supplied"}</td>
              <td><span className="badge">{r.triage}</span>{r.rejection_reason && <span className="badge err">{r.rejection_reason}</span>}
                <div className="muted">{r.intake?.delivery_suitability ?? ""}</div></td>
              <td>{r.next_action}</td>
            </tr>))}</tbody>
        </table>
      )}
    </section>
  );
}

const STAGES = ["identified", "contacted", "conversation", "proposal", "negotiation", "won", "lost"];
const KINDS = ["note", "email_sent", "email_received", "call", "meeting", "proposal_sent", "follow_up", "outcome"];

export function EngagementView({
  deals, board, onRefresh, onOpen,
}: { deals: Deal[]; board: BoardRow[]; onRefresh: () => void; onOpen: (id: string) => void }) {
  const [oppId, setOppId] = useState("");
  const [note, setNote] = useState("");
  const [kind, setKind] = useState("note");
  const [msg, setMsg] = useState<string | null>(null);
  const actionable = board.filter((r) => !r.is_mock && (r.triage === "actionable" || r.triage === "needs_verification"));
  const followUps = deals.filter((d) => d.next_follow_up_at && new Date(d.next_follow_up_at) <= new Date());

  async function startDeal() {
    if (!oppId) return;
    setMsg(null);
    try { await api.createDeal(oppId); setOppId(""); onRefresh(); }
    catch (e) { setMsg(e instanceof Error ? e.message : "failed"); }
  }

  return (
    <>
      <section className="card">
        <h2>Engagement pipeline</h2>
        <p className="muted">Track contact, responses, meetings, proposals and outcomes. Nothing is sent automatically — mark drafts sent only after you transmit them yourself.</p>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "end" }}>
          <label>Start deal from opportunity
            <select value={oppId} onChange={(e) => setOppId(e.target.value)} style={{ minWidth: 280 }}>
              <option value="">select…</option>
              {actionable.map((r) => <option key={r.id} value={r.id}>{r.title.slice(0, 80)}</option>)}
            </select>
          </label>
          <button onClick={() => void startDeal()}>Create / open deal</button>
          {msg && <span className="muted">{msg}</span>}
        </div>
        {followUps.length > 0 && <p className="err">{followUps.length} follow-up(s) due.</p>}
      </section>
      <section className="card" style={{ overflowX: "auto" }}>
        <h2>Deals ({deals.length})</h2>
        {deals.length === 0 ? <Empty what="deals" /> : (
          <table>
            <thead><tr><th>Opportunity</th><th>Stage</th><th>Value</th><th>Follow-up</th><th>Log activity</th><th>Recent</th></tr></thead>
            <tbody>{deals.map((d) => (
              <tr key={d.id}>
                <td><a href="#" onClick={(e) => { e.preventDefault(); onOpen(d.opportunity_id); }}>{d.opportunity_title ?? d.opportunity_id}</a></td>
                <td>
                  <select value={d.stage} onChange={(e) => void api.updateDeal(d.id, { stage: e.target.value }).then(onRefresh)}>
                    {STAGES.map((s) => <option key={s} value={s}>{s}</option>)}
                  </select>
                </td>
                <td>{d.value_usd ?? "unknown"}</td>
                <td>
                  <input type="date" defaultValue={d.next_follow_up_at ? d.next_follow_up_at.slice(0, 10) : ""}
                    onChange={(e) => void api.updateDeal(d.id, e.target.value
                      ? { next_follow_up_at: new Date(e.target.value).toISOString() }
                      : { clear_follow_up: true }).then(onRefresh)} />
                </td>
                <td>
                  <select value={kind} onChange={(e) => setKind(e.target.value)}>{KINDS.map((k) => <option key={k}>{k}</option>)}</select>
                  <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="summary" style={{ width: 160 }} />
                  <button onClick={() => {
                    if (!note.trim()) return;
                    void api.addInteraction(d.id, kind, note).then(() => { setNote(""); onRefresh(); });
                  }}>Add</button>
                </td>
                <td>{d.interactions.slice(0, 3).map((i) => (
                  <div className="muted" key={i.id}>{i.kind}: {i.summary.slice(0, 80)}</div>
                ))}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </section>
    </>
  );
}


export function FitPanel({ id, onChanged }: { id: string; onChanged: () => void }) {
  const [fit, setFit] = useState<import("@/lib/api").Fit | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setFit(null); setErr(null); api.fit(id).then(setFit).catch((e: unknown) => setErr(e instanceof Error ? e.message : "failed")); }, [id]);
  async function act(label: string, fn: () => Promise<unknown>) {
    setNote(null);
    try { await fn(); setNote(`${label}: done. Nothing was sent; it waits for your review.`); onChanged(); }
    catch (e) { setNote(`${label}: ${e instanceof Error ? e.message : "failed"}`); }
  }
  if (err) return <section className="card"><p className="err">Fit unavailable: {err}</p></section>;
  if (!fit) return <section className="card"><span className="muted">Loading fit…</span></section>;
  return (
    <section className="card">
      <h2>Fit &amp; approach <span className="badge">{fit.verdict}</span> <span className="badge">{fit.commercial_class_label}</span></h2>
      <p><b>Why relevant:</b> {fit.why_relevant}</p>
      <p><b>Problem:</b> {fit.problem}</p>
      <p><b>Buying intent:</b> {fit.buying_intent}</p>
      <p><b>Capabilities matched:</b> {fit.matched_capabilities.length === 0 ? "none" : fit.matched_capabilities.map((c) => `${c.label} (${c.evidence_status})`).join("; ")}</p>
      <p><b>What I could deliver:</b></p><ol>{fit.what_i_could_deliver.map((s) => <li key={s}>{s}</li>)}</ol>
      <p><b>Approach:</b> {fit.approach.angle}. {fit.approach.proposed_engagement}.</p>
      <p><b>Next action:</b> {fit.approach.next_action}</p>
      {fit.approach.cautions.map((c) => <div className="muted" key={c}>⚠ {c}</div>)}
      <p className="muted">Reasons: {fit.verdict_reasons.join(" · ")} · confidence {fit.confidence} · stage {STAGE_LABEL[fit.pipeline_stage] ?? fit.pipeline_stage}</p>
      {fit.missing.length > 0 && <p className="muted">Missing: {fit.missing.join("; ")}</p>}
      <p><b>Contact routes (as published):</b> {fit.contact_routes.length === 0 ? "none" : fit.contact_routes.map((r) => `${r.kind}: ${r.value}${r.role ? ` (${r.role})` : ""}`).join(", ")}</p>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button onClick={() => void act("Outreach draft", () => api.createDraft(id))}>Prepare outreach draft</button>
        <button onClick={() => void act("Deal", () => api.createDeal(id))}>Create deal</button>
        <button onClick={() => void act("Proposal draft", () => api.createProposal(id))}>Prepare proposal</button>
      </div>
      {note && <p className="muted">{note}</p>}
    </section>
  );
}

export function PipelineStages({ rows, proposals }: { rows: BoardRow[]; proposals: import("@/lib/api").ProposalRow[] }) {
  const real = rows.filter((r) => !r.is_mock);
  const order = ["discovered", "researched", "qualified", "outreach_prepared", "approved", "contacted", "responded", "meeting", "proposal", "negotiation", "won", "onboarding", "lost"];
  return (
    <section className="card">
      <h2>Acquisition pipeline (real records)</h2>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
        {order.map((s) => (
          <div className="card stat" key={s}><span className="muted">{STAGE_LABEL[s]}</span><b>{real.filter((r) => r.pipeline_stage === s).length}</b></div>
        ))}
      </div>
      <h2>Proposals ({proposals.length})</h2>
      <p className="muted">Approval is an admin action on the API; sending is done by you, outside this system.</p>
      {proposals.length === 0 ? <p className="muted">None yet.</p> : (
        <table><thead><tr><th>Proposal</th><th>Status</th><th>Price</th><th>Open items</th></tr></thead>
          <tbody>{proposals.map((p) => (
            <tr key={p.id}><td>{p.title}</td><td><span className="badge">{p.status}</span></td>
              <td>{p.price_text ?? "not set"}</td><td>{p.has_placeholders ? "placeholders remain" : "ready for approval"}</td></tr>))}</tbody></table>
      )}
    </section>
  );
}
