"use client";

import { useEffect, useState } from "react";
import {
  api, outcomeLabel, type BoardRow, type Deal, type Draft, type OnboardingItem, type ProposalRow,
  type ReviewItem, type Run, type SchedulesResponse,
} from "@/lib/api";

async function guard(setMsg: (m: string) => void, fn: () => Promise<unknown>, ok: string) {
  try { await fn(); setMsg(ok); } catch (e) { setMsg(e instanceof Error ? `Failed: ${e.message}` : "Failed"); }
}

/** Review queue: newly found, unreviewed records; human verification with explicit attestation. */
export function ReviewQueueView({
  queue, onOpen, onChanged,
}: { queue: ReviewItem[]; onOpen: (id: string) => void; onChanged: () => void }) {
  const [attest, setAttest] = useState<Record<string, string[]>>({});
  const [msg, setMsg] = useState("");
  const toggle = (id: string, k: string) =>
    setAttest((a) => ({ ...a, [id]: a[id]?.includes(k) ? a[id].filter((x) => x !== k) : [...(a[id] ?? []), k] }));
  return (
    <section className="card">
      <h2>Review queue — new, unreviewed opportunities ({queue.length})</h2>
      <p className="muted">
        Verifying means YOU opened the original source and it states a current request. Attest supplier eligibility
        or delivery model only if the source itself establishes them. Verification never overrides a failed
        criterion (closed, expired, out of scope) — the assessment is recomputed immediately.
      </p>
      {queue.length === 0 && <p className="muted">Nothing awaiting review.</p>}
      {queue.map((q) => (
        <div className="item" key={q.opportunity_id}>
          <a href="#" onClick={(e) => { e.preventDefault(); onOpen(q.opportunity_id); }}>{q.title}</a>{" "}
          <span className="badge">{q.pursuability.replace("_", " ")}</span>
          <span className="badge">{q.commercial_class}</span>
          <div className="muted">{q.sources.join(", ")} · first seen {new Date(q.first_seen_at).toLocaleDateString()}</div>
          {q.missing.map((m) => <div className="muted" key={m}>missing: {m}</div>)}
          <label><input type="checkbox" checked={attest[q.opportunity_id]?.includes("supplier_eligibility") ?? false}
            onChange={() => toggle(q.opportunity_id, "supplier_eligibility")} /> source establishes supplier eligibility</label>{" "}
          <label><input type="checkbox" checked={attest[q.opportunity_id]?.includes("delivery_model") ?? false}
            onChange={() => toggle(q.opportunity_id, "delivery_model")} /> source allows remote / subcontract delivery</label>{" "}
          <button onClick={() => void guard(setMsg, async () => { await api.verify(q.opportunity_id, attest[q.opportunity_id] ?? []); onChanged(); }, "Verified; assessment recomputed")}>
            I verified the source
          </button>
        </div>
      ))}
      {msg && <p className="muted">{msg}</p>}
    </section>
  );
}

/** Outreach drafts: edit, approve (admin token, server side), reject, record a manual send. */
export function DraftsView({ drafts, onChanged }: { drafts: Draft[]; onChanged: () => void }) {
  const [edit, setEdit] = useState<Record<string, { subject: string; body: string }>>({});
  const [msg, setMsg] = useState("");
  return (
    <section className="card">
      <h2>Outreach drafts ({drafts.length})</h2>
      <p className="muted">
        Nothing is transmitted from here. Approval is recorded under the authenticated approver; “Mark sent”
        only records that YOU sent it yourself.
      </p>
      {drafts.map((d) => {
        const e = edit[d.id] ?? { subject: d.subject ?? "", body: d.body };
        const editable = d.status === "pending_approval";
        return (
          <div className="item" key={d.id}>
            <span className="badge">{d.status}</span>
            <input aria-label="subject" disabled={!editable} value={e.subject} style={{ width: "100%" }}
              onChange={(ev) => setEdit({ ...edit, [d.id]: { ...e, subject: ev.target.value } })} />
            <textarea aria-label="body" disabled={!editable} value={e.body} rows={9} style={{ width: "100%" }}
              onChange={(ev) => setEdit({ ...edit, [d.id]: { ...e, body: ev.target.value } })} />
            {e.body.includes("[") && <div className="err">Contains [PLACEHOLDER] text: fill it in before approval.</div>}
            {editable && <>
              <button onClick={() => void guard(setMsg, async () => { await api.editDraft(d.id, { subject: e.subject, body: e.body }); onChanged(); }, "Draft saved")}>Save edits</button>{" "}
              <button onClick={() => void guard(setMsg, async () => { await api.editDraft(d.id, { subject: e.subject, body: e.body }); await api.approveDraft(d.id); onChanged(); }, "Approved")}>Save &amp; approve (admin)</button>{" "}
              <button onClick={() => void guard(setMsg, async () => { await api.rejectDraft(d.id); onChanged(); }, "Rejected")}>Reject</button>
            </>}
            {d.status === "approved" && (
              <button onClick={() => void guard(setMsg, async () => { await api.markDraftSent(d.id); onChanged(); }, "Recorded as sent by you")}>
                I sent this myself — record it
              </button>
            )}
          </div>
        );
      })}
      {msg && <p className="muted">{msg}</p>}
    </section>
  );
}

/** Proposals: review the full text, set scope/price/timeline in the body, approve, mark sent. */
export function ProposalsView({ proposals, onChanged }: { proposals: ProposalRow[]; onChanged: () => void }) {
  const [edit, setEdit] = useState<Record<string, { body: string; price: string }>>({});
  const [msg, setMsg] = useState("");
  return (
    <section className="card">
      <h2>Proposals / statements of work ({proposals.length})</h2>
      <p className="muted">
        Edit scope, price and timeline here. A proposal with placeholders or no price cannot be approved. Approval is
        an admin action; sending is always manual.
      </p>
      {proposals.map((p) => {
        const e = edit[p.id] ?? { body: p.body, price: p.price_text ?? "" };
        const editable = p.status === "draft";
        return (
          <div className="item" key={p.id}>
            <b>{p.title}</b> <span className="badge">{p.status}</span>
            {p.approved_by && <span className="muted"> approved by {p.approved_by}</span>}
            <label>Price / commercial terms
              <input disabled={!editable} value={e.price} style={{ width: "100%" }} placeholder="e.g. fixed fee 6,500 USD, 50% upfront"
                onChange={(ev) => setEdit({ ...edit, [p.id]: { ...e, price: ev.target.value } })} />
            </label>
            <textarea aria-label="proposal body" disabled={!editable} rows={14} style={{ width: "100%" }} value={e.body}
              onChange={(ev) => setEdit({ ...edit, [p.id]: { ...e, body: ev.target.value } })} />
            {p.has_placeholders && <div className="err">Still has [placeholders] or no price: complete before approval.</div>}
            {editable && <>
              <button onClick={() => void guard(setMsg, async () => { await api.editProposal(p.id, { body: e.body, price_text: e.price }); onChanged(); }, "Proposal saved")}>Save</button>{" "}
              <button onClick={() => void guard(setMsg, async () => { await api.editProposal(p.id, { body: e.body, price_text: e.price }); await api.approveProposal(p.id); onChanged(); }, "Approved")}>Save &amp; approve (admin)</button>
            </>}
            {p.status === "approved" && (
              <button onClick={() => void guard(setMsg, async () => { await api.proposalSent(p.id); onChanged(); }, "Recorded as sent by you")}>
                I sent this myself — record it
              </button>
            )}
          </div>
        );
      })}
      {msg && <p className="muted">{msg}</p>}
    </section>
  );
}

/** Onboarding checklist for won deals. */
export function OnboardingView({ deals }: { deals: Deal[] }) {
  const won = deals.filter((d) => d.stage === "won" || d.stage === "onboarding");
  const [items, setItems] = useState<Record<string, OnboardingItem[]>>({});
  const [msg, setMsg] = useState("");
  const wonKey = won.map((d) => d.id).join(",");
  useEffect(() => {
    void Promise.all(won.map(async (d) => [d.id, await api.onboarding(d.id)] as const))
      .then((rows) => setItems(Object.fromEntries(rows))).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wonKey]);
  return (
    <section className="card">
      <h2>Client onboarding</h2>
      {won.length === 0 && <p className="muted">No won deals yet. Onboarding opens only after a deal is marked won — it is never created for a deal that has not been won.</p>}
      {won.map((d) => (
        <div className="item" key={d.id}>
          <b>{d.opportunity_title}</b>
          {(items[d.id] ?? []).length === 0 && (
            <button onClick={() => void guard(setMsg, async () => { setItems({ ...items, [d.id]: await api.startOnboarding(d.id) }); }, "Checklist created")}>Start checklist</button>
          )}
          {(items[d.id] ?? []).map((i) => (
            <label key={i.id} style={{ display: "block" }}>
              <input type="checkbox" checked={i.done}
                onChange={(ev) => void guard(setMsg, async () => {
                  const row = await api.tickOnboarding(i.id, ev.target.checked);
                  setItems({ ...items, [d.id]: items[d.id].map((x) => (x.id === i.id ? row : x)) });
                }, "Updated")} /> {i.label}{i.done_by ? <span className="muted"> — {i.done_by}</span> : null}
            </label>
          ))}
        </div>
      ))}
      {msg && <p className="muted">{msg}</p>}
    </section>
  );
}

/** Scheduled discovery: per-source cadence, freshness and failures. Paid sources stay locked. */
export function AutomationView({
  data, runs, onChanged,
}: { data: SchedulesResponse | null; runs: Run[]; onChanged: () => void }) {
  const [msg, setMsg] = useState("");
  if (!data) return <p className="muted">Loading schedules…</p>;
  return (
    <section className="card" style={{ overflowX: "auto" }}>
      <h2>Autonomous discovery</h2>
      <p className="muted">
        Scheduler is <b>{data.scheduler_enabled ? "ON" : "OFF"}</b> (SCHEDULER_ENABLED, set on the worker). Paid
        providers are {data.paid_scheduling_allowed ? "allowed" : "locked"}: they are never scheduled without the
        owner’s explicit flag, per-provider approval and a budget.
      </p>
      <table>
        <thead><tr><th>Source</th><th>Channel</th><th>Every</th><th>Freshness</th><th>Last success</th><th>Last got / new</th><th>Failures</th><th /></tr></thead>
        <tbody>
          {data.sources.map((s) => (
            <tr key={s.connector}>
              <td>{s.connector} {s.paid && <span className="badge err">paid</span>}</td>
              <td>{s.channel}</td>
              <td>{s.interval_minutes >= 1440 ? `${(s.interval_minutes / 1440).toFixed(1)} d` : `${s.interval_minutes / 60} h`}</td>
              <td><span className={`badge ${s.freshness === "failing" || s.freshness === "stale" ? "err" : ""}`}>{s.freshness}</span></td>
              <td>{s.last_success_at ? new Date(s.last_success_at).toLocaleString() : "never"}</td>
              <td>{s.last_retrieved} / {s.last_new}</td>
              <td>{s.consecutive_failures}{s.last_error ? <div className="err">{s.last_error}</div> : null}</td>
              <td>
                <button disabled={s.paid && !data.paid_scheduling_allowed}
                  onClick={() => void guard(setMsg, async () => { await api.patchSchedule(s.connector, { enabled: !s.enabled }); onChanged(); }, "Schedule updated")}>
                  {s.enabled ? "Disable" : "Enable"} (admin)
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {msg && <p className="muted">{msg}</p>}
      <h3>Recent run outcomes</h3>
      {runs.slice(0, 8).map((r) => (
        <div className="item" key={r.id}>
          <span className="badge">{outcomeLabel(r)}</span> {r.trigger ?? "manual"} · {new Date(r.created_at).toLocaleString()}
          {r.state.outcome && <div className="muted">{r.state.outcome.detail} · new {r.state.outcome.new_records} · pursuable after triage {r.state.outcome.pursuable_after_triage}</div>}
        </div>
      ))}
    </section>
  );
}

export type { BoardRow };
