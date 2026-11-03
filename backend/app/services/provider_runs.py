"""Durable record of every billable provider run, written BEFORE the submit request.

Guarantees
* one row per Actor run, so several runs under one spend reservation can never overwrite each
  other's provider ids (the old single-string annotation could);
* an idempotency key makes a second submission of the same work impossible: if the first
  submission's outcome is uncertain the run is NEVER re-submitted, it must be reconciled;
* real charges are derived from the provider's own run record (usage and charged events); a
  figure the provider has not yet reported is never replaced by a guess - the row keeps its
  `estimate` basis and the spend reservation stays in place.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.base import ProviderRunPending
from app.models import ProviderRun, ProviderSpend
from app.utils import utcnow

FINAL = {"succeeded", "failed", "rejected"}
APIFY_DONE = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}


class ProviderRunUncertain(ProviderRunPending):
    """An earlier submission may have started a billable run we have no id for. Re-submitting
    could bill twice, so the work is refused until a human reconciles it."""

    def __init__(self, message: str) -> None:
        super().__init__(message, "")


def idempotency_key(
    workflow_run_id: uuid.UUID | None, provider: str, actor: str, run_input: Any
) -> str:
    blob = json.dumps(run_input, sort_keys=True, default=str)
    raw = f"{workflow_run_id or 'cli'}|{provider}|{actor}|{blob}"
    return hashlib.sha256(raw.encode()).hexdigest()[:48]


def charge_of(run: dict[str, Any]) -> dict[str, Any]:
    """Best known REAL charge from an Apify run object.

    `usageTotalUsd` can lag pay-per-event/result charges (observed: $0.00005 reported vs $0.076
    billed). So the figure is the larger of the reported usage and the sum of charged events x
    their published event price. When the provider reports neither, the basis stays `estimate`.
    """
    reported = run.get("usageTotalUsd")
    reported_f = float(reported) if isinstance(reported, int | float) else None
    events = run.get("chargedEventCounts") or {}
    prices = ((run.get("pricingInfo") or {}).get("pricingPerEvent") or {}).get(
        "actorChargeEvents"
    ) or {}
    event_usd: float | None = None
    if isinstance(events, dict) and events and isinstance(prices, dict):
        total, complete = 0.0, True
        for name, count in events.items():
            price = (prices.get(name) or {}).get("eventPriceUsd")
            if isinstance(price, int | float) and isinstance(count, int | float):
                total += float(count) * float(price)
            else:
                complete = False
        event_usd = total if complete else None
    known = [v for v in (reported_f, event_usd) if v is not None]
    if not known:
        return {"reported": None, "events": None, "charged": None, "basis": "estimate"}
    basis = "events" if event_usd is not None and event_usd >= (reported_f or 0.0) else "usage"
    return {
        "reported": reported_f,
        "events": event_usd,
        "charged": round(max(known), 6),
        "basis": basis,
    }


class RunLedger:
    """Bound to one spend reservation + workflow run; used by the Apify client around each run."""

    def __init__(
        self,
        session: Session,
        provider: str,
        workflow_run_id: uuid.UUID | None,
        spend_id: uuid.UUID | None,
        on_submitted: Any = None,
    ) -> None:
        self.on_submitted = on_submitted
        self.session = session
        self.provider = provider
        self.workflow_run_id = workflow_run_id
        self.spend_id = spend_id
        self.runs: list[uuid.UUID] = []

    # Each write commits independently on PostgreSQL so another worker/crash can see it.
    def _write(self, fn: Any) -> Any:
        bind = self.session.get_bind()
        if bind.dialect.name == "postgresql":
            with Session(bind) as s:
                out = fn(s)
                s.commit()
                return out
        out = fn(self.session)
        self.session.flush()
        return out

    def begin(self, actor: str, run_input: Any, max_charge: float) -> tuple[uuid.UUID, str | None]:
        """Reserve the right to submit. Returns (row id, existing provider run id to ATTACH to).
        Raises ProviderRunUncertain when a prior attempt may have started an unknown run."""
        key = idempotency_key(self.workflow_run_id, self.provider, actor, run_input)

        def op(s: Session) -> tuple[uuid.UUID, str | None]:
            row = s.execute(
                select(ProviderRun).where(ProviderRun.idempotency_key == key)
            ).scalar_one_or_none()
            if row is None:
                row = ProviderRun(
                    provider=self.provider,
                    actor=actor,
                    idempotency_key=key,
                    workflow_run_id=self.workflow_run_id,
                    spend_id=self.spend_id,
                    max_charge_usd=max_charge,
                    status="submitting",
                )
                s.add(row)
                s.flush()
                return row.id, None
            if row.status == "rejected":  # provider refused before doing work: safe to retry
                row.status, row.spend_id = "submitting", self.spend_id
                return row.id, None
            if row.external_id:
                row.spend_id = row.spend_id or self.spend_id
                return row.id, row.external_id  # attach to the existing run, never resubmit
            raise ProviderRunUncertain(
                f"an earlier submission of this {actor} run has an unknown outcome "
                f"(provider_runs.id={row.id}); not re-submitting - reconcile it first"
            )

        rid, existing = self._write(op)
        self.runs.append(rid)
        return rid, existing

    def _update(self, row_id: uuid.UUID, **values: Any) -> None:
        def op(s: Session) -> None:
            row = s.get(ProviderRun, row_id)
            assert row is not None
            for k, v in values.items():
                setattr(row, k, v)

        self._write(op)

    def submitted(self, row_id: uuid.UUID, run: dict[str, Any]) -> None:
        self._update(
            row_id,
            external_id=str(run["id"]),
            dataset_id=run.get("defaultDatasetId"),
            status="submitted",
        )
        if self.on_submitted is not None:
            self.on_submitted(str(run["id"]))

    def rejected(self, row_id: uuid.UUID, why: str) -> None:
        self._update(row_id, status="rejected", detail={"rejected": why[:240]})

    def uncertain(self, row_id: uuid.UUID, why: str) -> None:
        self._update(row_id, status="unknown", detail={"uncertain": why[:240]})

    def finished(self, row_id: uuid.UUID, run: dict[str, Any]) -> None:
        apply_provider_record(self.session, row_id, run, write=self._write)


def apply_provider_record(
    session: Session, row_id: uuid.UUID, run: dict[str, Any], write: Any = None
) -> dict[str, Any]:
    """Store the provider's own record of a run on its row. Final only when the provider says so."""
    c = charge_of(run)
    status = run.get("status")
    final = status in APIFY_DONE

    def op(s: Session) -> None:
        row = s.get(ProviderRun, row_id)
        assert row is not None
        row.external_id = str(run.get("id") or row.external_id)
        row.dataset_id = run.get("defaultDatasetId") or row.dataset_id
        row.reported_usd, row.event_usd = c["reported"], c["events"]
        if c["charged"] is not None:
            row.charged_usd, row.cost_basis = c["charged"], c["basis"]
        if final:
            row.status = "succeeded" if status == "SUCCEEDED" else "failed"
            row.finalized_at = utcnow()
        row.detail = {**(row.detail or {}), "provider_status": status}

    if write is not None:
        write(op)
    else:
        op(session)
        session.flush()
    return c


def settle_spend_from_runs(session: Session, spend_id: uuid.UUID) -> float | None:
    """When EVERY run under a reservation is final with a real charge, replace the reservation by
    the sum of those charges. Otherwise leave it untouched (never guess)."""
    runs = list(
        session.execute(select(ProviderRun).where(ProviderRun.spend_id == spend_id)).scalars()
    )
    spend = session.get(ProviderSpend, spend_id)
    if (
        not runs
        or spend is None
        or any(r.status not in {"succeeded", "failed"} or r.charged_usd is None for r in runs)
    ):
        return None
    total = round(sum(float(r.charged_usd or 0) for r in runs), 4)
    spend.amount_usd = total
    spend.description = "reconciled:" + ";".join(f"apify_run:{r.external_id}" for r in runs)
    session.flush()
    return total
