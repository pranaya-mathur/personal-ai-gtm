"""Reconcile billable provider runs against the provider's OWN records, without re-running them.

    python -m app.cli.reconcile                       # dry run (reads only, writes nothing)
    python -m app.cli.reconcile --apply               # write real charges, settle reservations
    python -m app.cli.reconcile --billing             # also compare the ledger with Apify's
                                                      #   monthly usage figure (read-only GET)
    python -m app.cli.reconcile --attach ROW=RUNID    # link an uncertain submission to a run id
                                                      #   you found in the Apify console

Rules: only free GET requests; a run that is not finished stays reserved; a run whose charge the
provider has not reported keeps its reservation (never replaced by an estimate); a submission with
no run id is reported as UNRESOLVED and is never re-submitted.
"""

import argparse
import math
import re
import uuid
from typing import Any

from sqlalchemy import select

from app.connectors.apify_client import ApifyClient
from app.core.config import Settings, get_settings
from app.db.session import get_session_factory
from app.models import ProviderRun, ProviderSpend
from app.services.provider_runs import (
    APIFY_DONE,
    apply_provider_record,
    charge_of,
    settle_spend_from_runs,
)

_RUN = re.compile(r"apify_run:([A-Za-z0-9]+)")


def reconcile(session, client: ApifyClient, apply: bool) -> list[dict[str, object]]:  # type: ignore[no-untyped-def]
    out: list[dict[str, object]] = []
    covered: set[str] = set()
    spend_ids: set[uuid.UUID] = set()
    for row in session.execute(select(ProviderRun).order_by(ProviderRun.created_at)).scalars():
        if row.external_id:
            covered.add(row.external_id)
        if row.status in ("succeeded", "failed", "rejected") and row.charged_usd is not None:
            continue
        if not row.external_id:
            if row.status != "rejected":
                out.append(
                    {
                        "provider_run": str(row.id),
                        "action": "UNRESOLVED: submit outcome unknown, not re-submitting; find the "
                        "run in the Apify console and use --attach",
                        "reserved_usd": float(row.max_charge_usd),
                    }
                )
            continue
        run = client.fetch_run(row.external_id)
        c = charge_of(run)
        entry: dict[str, object] = {
            "provider_run": str(row.id),
            "run_id": row.external_id,
            "status": run.get("status"),
            "provider_usage_usd": c["reported"],
            "event_usd": c["events"],
            "charged_usd": c["charged"],
            "basis": c["basis"],
        }
        done = run.get("status") in APIFY_DONE
        entry["action"] = (
            "record real charge" if done and c["charged"] is not None else "leave reserved"
        )
        if apply:
            apply_provider_record(session, row.id, run)
            if row.spend_id:
                spend_ids.add(row.spend_id)
        out.append(entry)
    for sid in spend_ids:
        total = settle_spend_from_runs(session, sid)
        out.append({"spend": str(sid), "action": "settled" if total is not None else "left reserved",
                    "total_usd": total})  # fmt: skip
    # legacy reservations written before provider_runs existed (single run id in the description)
    for row in session.execute(
        select(ProviderSpend).where(ProviderSpend.description.like("%apify_run:%"))
    ).scalars():
        if row.description.startswith("reconciled:"):
            continue
        ids = [i for i in _RUN.findall(row.description) if i not in covered]
        if not ids or len(ids) != len(_RUN.findall(row.description)):
            continue
        runs = [client.fetch_run(i) for i in ids]
        charges = [charge_of(r) for r in runs]
        done = all(r.get("status") in APIFY_DONE for r in runs) and all(
            c["charged"] is not None for c in charges
        )
        entry = {
            "run_id": ",".join(ids),
            "status": ",".join(str(r.get("status")) for r in runs),
            "reserved_usd": float(row.amount_usd),
            "provider_usage_usd": sum(c["reported"] or 0 for c in charges),
            "action": "settle" if done else "leave reserved (not finished or usage unknown)",
        }
        if done and apply:
            row.amount_usd = math.ceil(sum(c["charged"] for c in charges) * 10_000) / 10_000
            row.description = "reconciled:" + ";".join(
                f"apify_run:{i} status={r.get('status')}" for i, r in zip(ids, runs, strict=True)
            )
        out.append(entry)
    session.flush()
    return out


def attach(session, row_id: uuid.UUID, run_id: str, client: ApifyClient) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    row = session.get(ProviderRun, row_id)
    if row is None or row.external_id:
        raise SystemExit("unknown provider run, or it already has a provider run id")
    run = client.fetch_run(run_id)
    row.external_id = run_id
    return {"attached": run_id, "charge": apply_provider_record(session, row.id, run)}


def billing_check(session, client: ApifyClient) -> dict[str, object]:  # type: ignore[no-untyped-def]
    """Compare the local Apify ledger with the provider's own monthly usage total (read-only)."""
    from sqlalchemy import func

    ledger = float(
        session.execute(
            select(func.coalesce(func.sum(ProviderSpend.amount_usd), 0)).where(
                ProviderSpend.provider.like("apify%")
            )
        ).scalar_one()
    )
    usage = client.monthly_usage()
    return {"ledger_apify_usd": round(ledger, 4), "provider_monthly_usage": usage}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--billing", action="store_true")
    ap.add_argument("--attach", metavar="ROW=RUNID")
    args = ap.parse_args()
    s: Settings = get_settings()
    if s.apify_token is None:
        raise SystemExit("APIFY_TOKEN is not set")
    client = ApifyClient(s.apify_token, s.apify_api_base)
    with get_session_factory()() as session:
        if args.attach:
            row, _, rid = args.attach.partition("=")
            print(attach(session, uuid.UUID(row), rid, client))
        else:
            for e in reconcile(session, client, args.apply):
                print(e)
        if args.billing:
            print(billing_check(session, client))
        if args.apply or args.attach:
            session.commit()
        else:
            session.rollback()


if __name__ == "__main__":
    main()
