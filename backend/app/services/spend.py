import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import ProviderSpend


class SpendLimitExceeded(Exception):
    pass


_LOCK_KEY = 7_340_001  # arbitrary constant for pg_advisory_xact_lock


@dataclass(frozen=True)
class Reservation:
    id: uuid.UUID
    amount_usd: float


def _round_up(usd: float) -> float:
    return math.ceil(usd * 10_000) / 10_000


class SpendGuard:
    """Blocks a provider call BEFORE it happens if the estimate would breach a limit."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def _total(
        self,
        since: datetime | None = None,
        run_id: uuid.UUID | None = None,
        family: str | None = None,
    ) -> float:
        stmt = select(func.coalesce(func.sum(ProviderSpend.amount_usd), 0))
        if family is not None:
            stmt = stmt.where(ProviderSpend.provider.like(f"{family}%"))
        if since is not None:
            stmt = stmt.where(ProviderSpend.created_at >= since)
        if run_id is not None:
            stmt = stmt.where(ProviderSpend.run_id == run_id)
        return float(self.session.execute(stmt).scalar_one())

    def check(
        self, estimated_usd: float, run_id: uuid.UUID | None, family: str | None = None
    ) -> None:
        """`family` is the provider prefix ("openai" / "apify") for the controlled-test caps."""
        s = self.settings
        if family is not None:
            cap = {
                "openai": s.openai_test_budget_usd,
                "apify": s.apify_test_budget_usd,
                "brave": s.search_budget_usd,
            }.get(family)
            if cap is not None and self._total(family=family) + estimated_usd > cap:
                raise SpendLimitExceeded(f"{family} test budget (${cap:.2f}) would be exceeded")
        if (
            s.total_test_budget_usd is not None
            and self._total() + estimated_usd > s.total_test_budget_usd
        ):
            raise SpendLimitExceeded(
                f"total test budget (${s.total_test_budget_usd:.2f}) would be exceeded"
            )
        now = datetime.now(UTC)
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month = day.replace(day=1)
        if (
            run_id is not None
            and self._total(run_id=run_id) + estimated_usd > s.spend_limit_per_run_usd
        ):
            raise SpendLimitExceeded("per-run spend limit would be exceeded")
        if self._total(since=day) + estimated_usd > s.spend_limit_daily_usd:
            raise SpendLimitExceeded("daily spend limit would be exceeded")
        if self._total(since=month) + estimated_usd > s.spend_limit_monthly_usd:
            raise SpendLimitExceeded("monthly spend limit would be exceeded")

    def record(
        self, provider: str, amount_usd: float, run_id: uuid.UUID | None, description: str = ""
    ) -> None:
        self.session.add(
            ProviderSpend(
                provider=provider, run_id=run_id, amount_usd=amount_usd, description=description
            )
        )
        self.session.flush()

    # ---- atomic reservations -------------------------------------------------------------
    def reserve(
        self,
        estimated_usd: float,
        run_id: uuid.UUID | None,
        family: str | None,
        provider: str,
        description: str,
    ) -> Reservation:
        """Check limits AND record the estimated spend in one serialized step, so concurrent
        workers cannot both pass the same remaining-budget check. On PostgreSQL this runs in its
        own short transaction under an advisory lock and commits immediately, making the
        reservation visible to every other worker before the paid call starts."""
        amount = _round_up(estimated_usd)
        bind = self.session.get_bind()
        if bind.dialect.name == "postgresql":
            with Session(bind) as s:
                s.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_KEY})
                SpendGuard(s, self.settings).check(amount, run_id, family)
                row = ProviderSpend(
                    provider=provider,
                    run_id=run_id,
                    amount_usd=amount,
                    description=f"reserved:{description}",
                )
                s.add(row)
                s.flush()
                rid = row.id
                s.commit()
            return Reservation(rid, amount)
        self.check(amount, run_id, family)
        row = ProviderSpend(
            provider=provider,
            run_id=run_id,
            amount_usd=amount,
            description=f"reserved:{description}",
        )
        self.session.add(row)
        self.session.flush()
        return Reservation(row.id, amount)

    def annotate(self, reservation: Reservation, note: str) -> None:
        """Attach the provider run id to the reserved row (committed immediately)."""
        stmt = (
            update(ProviderSpend)
            .where(ProviderSpend.id == reservation.id)
            .values(description=ProviderSpend.description + f";{note}")
        )
        bind = self.session.get_bind()
        if bind.dialect.name == "postgresql":
            with Session(bind) as s:
                s.execute(stmt)
                s.commit()
        else:
            self.session.execute(stmt)
            self.session.flush()

    def settle(self, reservation: Reservation, actual_usd: float) -> None:
        """Replace the estimate with the provider-reported/computed actual. Never called for
        failed or unmetered calls: those keep the full estimate (conservative)."""
        amount = _round_up(actual_usd)
        current = self.session.execute(
            select(ProviderSpend.description).where(ProviderSpend.id == reservation.id)
        ).scalar_one_or_none() or ""
        notes = current.partition(";")[2]  # keep the provider run ids recorded during the call
        stmt = (
            update(ProviderSpend)
            .where(ProviderSpend.id == reservation.id)
            .values(amount_usd=amount, description="settled" + (f";{notes}" if notes else ""))
        )
        bind = self.session.get_bind()
        if bind.dialect.name == "postgresql":
            with Session(bind) as s:
                s.execute(stmt)
                s.commit()
        else:
            self.session.execute(stmt)
            self.session.flush()
