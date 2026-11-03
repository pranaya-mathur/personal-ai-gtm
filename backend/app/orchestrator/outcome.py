"""Truthful result of a discovery run.

`status` says whether the machinery finished; `outcome` says whether anything was learned:

* succeeded            every source that ran worked and returned records
* no_matching_records  sources ran cleanly and returned nothing relevant (NOT a discovery)
* partial              some sources failed or were blocked, others worked
* failed               every source that ran failed/blocked, or nothing ran at all
* cancelled            cancelled by an operator

Sources that are switched off (`skipped`) are reported but are not failures.
"""

from typing import Any

SUCCEEDED = "succeeded"
NO_RECORDS = "no_matching_records"
PARTIAL = "partial"
FAILED = "failed"
CANCELLED = "cancelled"


def summarize(state: dict[str, Any]) -> dict[str, Any]:
    sources: dict[str, dict[str, Any]] = {}
    for s in state.get("connector_stats") or []:
        cur = sources.setdefault(
            s["connector"],
            {"status": "succeeded", "retrieved": 0, "created": 0, "duplicates": 0, "error": None},
        )
        # a connector reported by both agents is judged by its worst status
        rank = {"succeeded": 0, "skipped": 1, "blocked": 2, "failed": 3}
        if rank.get(s["status"], 3) > rank.get(cur["status"], 0):
            cur["status"] = s["status"]
        cur["error"] = cur["error"] or s.get("error") or s.get("note")
        for k in ("retrieved", "created", "duplicates"):
            cur[k] = max(cur[k], int(s.get(k, 0)))
    for d in state.get("source_discovery") or []:
        d_failed = bool(d.get("error"))
        sources[f"discoverer:{d['source']}"] = {
            "status": "skipped" if d.get("skipped") else ("failed" if d_failed else "succeeded"),
            "retrieved": int(d.get("found", 0)),
            "created": int(d.get("new", 0)),
            "duplicates": 0,
            "error": d.get("error"),
        }
    cand = state.get("candidate_stats") or {}
    if cand.get("checked"):
        bad = int(cand.get("error", 0)) + int(cand.get("blocked", 0))
        sources["candidate_pages"] = {
            "status": "failed" if bad == cand["checked"] else "succeeded",
            "retrieved": int(cand.get("signal", 0)),
            "created": int(cand.get("signal", 0)),
            "duplicates": 0,
            "error": f"{bad}/{cand['checked']} pages unreadable" if bad else None,
        }
    ran = {k: v for k, v in sources.items() if v["status"] != "skipped"}
    failed = [k for k, v in ran.items() if v["status"] in ("failed", "blocked")]
    retrieved = sum(int(v["retrieved"]) for v in ran.values())
    if not ran:
        outcome = FAILED
        detail = "no discovery source executed (all switched off or none configured)"
    elif len(failed) == len(ran):
        outcome = FAILED
        detail = f"all {len(ran)} executed source(s) failed: " + ", ".join(sorted(failed))
    elif failed:
        outcome = PARTIAL
        detail = f"{len(failed)} of {len(ran)} source(s) failed: " + ", ".join(sorted(failed))
    elif retrieved == 0:
        outcome = NO_RECORDS
        detail = f"{len(ran)} source(s) ran cleanly and returned no matching records"
    else:
        outcome = SUCCEEDED
        detail = f"{retrieved} record(s) retrieved from {len(ran)} source(s)"
    return {
        "outcome": outcome,
        "detail": detail,
        "sources_ran": len(ran),
        "sources_failed": sorted(failed),
        "sources_skipped": sorted(k for k, v in sources.items() if v["status"] == "skipped"),
        "retrieved": retrieved,
        "new_records": sum(int(v["created"]) for v in ran.values()),
        "duplicates": sum(int(v["duplicates"]) for v in ran.values()),
        "pursuable_after_triage": int((state.get("triage") or {}).get("actionable", 0)),
        "per_source": sources,
    }
