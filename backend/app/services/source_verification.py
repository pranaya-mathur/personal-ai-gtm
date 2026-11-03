import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Evidence, OpportunityEvidence
from app.models.enums import SourceCheckStatus
from app.services.source_check import SourceChecker


def verify_sources(
    session: Session, opportunity_ids: list[str], checker: SourceChecker
) -> dict[str, Any]:
    """Check each not-yet-checked evidence row once; results are stored on the evidence."""
    ids = [uuid.UUID(o) for o in opportunity_ids]
    evidence = (
        session.execute(
            select(Evidence)
            .join(OpportunityEvidence, OpportunityEvidence.evidence_id == Evidence.id)
            .where(
                OpportunityEvidence.opportunity_id.in_(ids),
                Evidence.source_check_status == SourceCheckStatus.NOT_CHECKED,
            )
            .distinct()
        )
        .scalars()
        .all()
    )
    counts: dict[str, int] = {}
    for ev in evidence:
        status = checker.check(ev.source_url, ev.original_text)
        ev.source_check_status = status
        ev.source_checked_at = datetime.now(UTC)
        counts[status.value] = counts.get(status.value, 0) + 1
    session.flush()
    return counts
