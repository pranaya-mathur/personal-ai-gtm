import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import ProviderSpend


class SpendLimitExceeded(Exception):
    pass


class SpendGuard:
    """Blocks a provider call BEFORE it happens if the estimate would breach a limit."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def _total(self, since: datetime | None = None, run_id: uuid.UUID | None = None) -> float:
        stmt = select(func.coalesce(func.sum(ProviderSpend.amount_usd), 0))
        if since is not None:
            stmt = stmt.where(ProviderSpend.created_at >= since)
        if run_id is not None:
            stmt = stmt.where(ProviderSpend.run_id == run_id)
        return float(self.session.execute(stmt).scalar_one())

    def check(self, estimated_usd: float, run_id: uuid.UUID | None) -> None:
        now = datetime.now(UTC)
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month = day.replace(day=1)
        s = self.settings
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
