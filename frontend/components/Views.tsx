"use client";

import { useEffect, useState } from "react";
import {
  api, jobTotals, splitReal, viewRows,
  type Analysis, type BoardRow, type CostSummary, type OpportunityDetail, type RecordView, type Run,
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
}: { rows: BoardRow[]; view: RecordView; title: string; blurb: string; onOpen: (id: string) => void }) {
  const mock = splitReal(rows).mock.filter((r) => r.view === view).length;
  const list = viewRows(rows, view);
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
                  <div className="muted">{r.verification_status}{r.deadline ? ` · deadline ${new Date(r.deadline).toLocaleDateString()}` : ""}</div></td>
                <td>{r.evidence_excerpt && <blockquote>{r.evidence_excerpt}</blockquote>}
                  {r.source_url && <a href={r.source_url} target="_blank" rel="noopener noreferrer">source ({r.evidence_count})</a>}</td>
                <td><Stage r={r} /></td>
                <td>{r.stated_budget ?? "unknown"}</td>
                <td>{r.missing.length === 0 ? "–" : <ul>{r.missing.map((m) => <li key={m}>{m}</li>)}</ul>}</td>
                <td>{r.next_action}</td>
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

export function ResearchDetail({ id, row }: { id: string | null; row: BoardRow | undefined }) {
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
  );
}
