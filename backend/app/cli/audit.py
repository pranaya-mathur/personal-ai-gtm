"""Re-assess existing records with the two-stage rules.

    python -m app.cli.audit                    # dry run, offline, prints what would change
    python -m app.cli.audit --apply            # writes the assessment + an audit Qualification row
    python -m app.cli.audit --backfill-apify   # also READS recent Apify run datasets (free GET, no
                                               # Actor is executed) to fill deadline/status/budget

Evidence, categories and human verification are never modified or deleted; every change appends a
Qualification row that records the previous status/triage.
"""

import argparse
from collections import Counter
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.apify_specs import SPECS
from app.connectors.base import DiscoveryQuery, RawFinding
from app.core.config import Settings, get_settings
from app.db.session import get_session_factory
from app.models import Evidence, Opportunity, OpportunityEvidence, Qualification
from app.services.dedupe import canonical_url
from app.services.triage import apply_assessment, assess_opportunity


def fetch_apify_findings(settings: Settings, max_runs: int = 10) -> list[RawFinding]:
    """Read-only: lists recent SUCCEEDED runs of the registered Actors and reads their datasets."""
    token = settings.apify_token.get_secret_value() if settings.apify_token else ""
    if not token:
        raise SystemExit("APIFY_TOKEN is not set")
    out: list[RawFinding] = []
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(base_url=settings.apify_api_base, headers=headers, timeout=30) as c:
        for spec in SPECS.values():
            if not spec.reviewed:
                continue
            slug = spec.actor_id.replace("/", "~")
            runs = c.get(
                f"/v2/acts/{slug}/runs",
                params={"limit": max_runs, "desc": 1, "status": "SUCCEEDED"},
            ).json()["data"]["items"]
            for run in runs:
                items = c.get(
                    f"/v2/datasets/{run['defaultDatasetId']}/items",
                    params={"format": "json", "clean": 1},
                ).json()
                for item in items:
                    f = spec.map_item(item, DiscoveryQuery())
                    if f is not None:
                        out.append(f)
    return out


def backfill(session: Session, findings: list[RawFinding]) -> int:
    by_url = {canonical_url(f.source_url): f for f in findings}
    touched = 0
    for opp in session.execute(select(Opportunity)).scalars():
        urls = session.execute(
            select(Evidence.source_url)
            .join(OpportunityEvidence, OpportunityEvidence.evidence_id == Evidence.id)
            .where(OpportunityEvidence.opportunity_id == opp.id)
        ).scalars()
        f = next((by_url[canonical_url(u)] for u in urls if canonical_url(u) in by_url), None)
        if f is None:
            continue
        changed = False
        if f.source_status != "unknown" and opp.source_status != f.source_status:
            opp.source_status, changed = f.source_status, True
        for attr in ("deadline", "budget_text", "contact_route"):
            if getattr(opp, attr) is None and getattr(f, attr) is not None:
                setattr(opp, attr, getattr(f, attr))
                changed = True
        touched += changed
    session.flush()
    return touched


def audit(session: Session, settings: Settings, apply: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for opp in session.execute(select(Opportunity).order_by(Opportunity.created_at)).scalars():
        a = assess_opportunity(session, opp, settings)
        before = {"status": opp.status, "triage": opp.triage}
        after: dict[str, Any] = {
            "triage": str(a.triage),
            "relevance": str(a.technical_relevance),
        }
        row: dict[str, Any] = {
            "id": str(opp.id),
            "title": opp.title,
            "category": opp.category,
            "before": before,
            "after": after,
            "reasons": a.reasons,
            "missing": a.missing,
            "rejection_reason": a.rejection_reason,
        }
        if apply:
            previous = apply_assessment(opp, a)
            last = session.execute(
                select(Qualification.score)
                .where(Qualification.opportunity_id == opp.id)
                .order_by(Qualification.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            session.add(
                Qualification(
                    opportunity_id=opp.id,
                    score=float(last or 0),  # ranking score carries over; the audit sets status
                    criteria={"audit": True, "assessment": a.to_json(), "previous": previous},
                    rationale=f"audit: {a.triage}"
                    + (f" ({a.rejection_reason})" if a.rejection_reason else "")
                    + f"; previous status={previous['status']}, triage={previous['triage']}",
                )
            )
            after["status"] = opp.status
        rows.append(row)
    session.flush()
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--backfill-apify", action="store_true")
    args = ap.parse_args()
    settings = get_settings()
    with get_session_factory()() as session:
        if args.backfill_apify:
            n = backfill(session, fetch_apify_findings(settings))
            print(f"backfilled structured fields on {n} opportunities (read-only dataset reads)")
        rows = audit(session, settings, args.apply)
        counts = Counter(r["after"]["triage"] for r in rows)
        for r in rows:
            print(
                f"{r['after']['triage']:<18} {r['before']['status']:>12} -> "
                f"{r['after'].get('status', '(dry run)'):<12} {r['title'][:70]}"
                + (f"  [{r['rejection_reason']}]" if r["rejection_reason"] else "")
            )
        print(dict(counts), "APPLIED" if args.apply else "DRY RUN (use --apply)")
        if args.apply:
            session.commit()
        else:
            session.rollback()


if __name__ == "__main__":
    main()
