"""Reconcile billable provider runs whose outcome was unknown (timeouts), WITHOUT re-running them.

    python -m app.cli.reconcile            # dry run
    python -m app.cli.reconcile --apply    # settle the ledger from the provider's own run record

Only reads run records from the provider (free GETs). Unfinished runs are left reserved.
"""

import argparse
import re

from sqlalchemy import select

from app.connectors.apify_client import ApifyClient
from app.core.config import Settings, get_settings
from app.db.session import get_session_factory
from app.models import ProviderSpend

_RUN = re.compile(r"apify_run:([A-Za-z0-9]+)")
_DONE = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}


def reconcile(session, client: ApifyClient, apply: bool) -> list[dict[str, object]]:  # type: ignore[no-untyped-def]
    out: list[dict[str, object]] = []
    rows = session.execute(
        select(ProviderSpend).where(ProviderSpend.description.like("reserved;%apify_run:%"))
    ).scalars()
    for row in rows:
        m = _RUN.search(row.description)
        if not m:
            continue
        run = client.fetch_run(m.group(1))
        status, usage = run.get("status"), run.get("usageTotalUsd")
        entry: dict[str, object] = {
            "run_id": m.group(1),
            "status": status,
            "reserved_usd": float(row.amount_usd),
            "provider_usage_usd": usage,
        }
        if status in _DONE and isinstance(usage, int | float):
            entry["action"] = "settle"
            if apply:
                row.amount_usd = round(float(usage) + 0.00005, 4)
                row.description = f"reconciled:apify_run:{m.group(1)} status={status}"
        else:
            entry["action"] = "leave reserved (not finished or usage unknown)"
        out.append(entry)
    session.flush()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    s: Settings = get_settings()
    if s.apify_token is None:
        raise SystemExit("APIFY_TOKEN is not set")
    client = ApifyClient(s.apify_token, s.apify_api_base)
    with get_session_factory()() as session:
        for e in reconcile(session, client, args.apply):
            print(e)
        if args.apply:
            session.commit()
        else:
            session.rollback()


if __name__ == "__main__":
    main()
