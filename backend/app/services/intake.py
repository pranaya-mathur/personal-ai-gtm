"""Human-reviewed lead intake: a person supplies a URL and a permitted excerpt (e.g. copied from a
post they are allowed to read). Nothing is fetched, scraped or inferred; missing fields stay
unknown. Records carry provider "manual_intake" so they stay separate from automatic discovery."""

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.connectors.base import RawFinding, RawRoute, SignalKind
from app.core.config import Settings
from app.models import Opportunity, Qualification
from app.services.ingest import ingest_finding
from app.services.routes import route_kind
from app.services.triage import apply_assessment, assess_opportunity

PROVIDER = "manual_intake"
PLATFORMS = {"reddit", "linkedin", "facebook", "slack", "discord", "forum", "email", "other"}


class IntakeError(ValueError):
    pass


def intake_lead(
    session: Session, settings: Settings, who: str, data: dict[str, Any]
) -> Opportunity:
    url = str(data.get("source_url") or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise IntakeError("source_url must be an http(s) link to the original post")
    excerpt = str(data.get("excerpt") or "").strip()
    requirement = str(data.get("requirement") or "").strip()
    if len(excerpt) < 30 or len(requirement) < 10:
        raise IntakeError("provide the requirement and a verbatim excerpt (at least 30 characters)")
    channel = data.get("channel") or "direct_buyer"
    if channel not in ("direct_buyer", "agency"):
        raise IntakeError("channel must be direct_buyer or agency")
    platform = str(data.get("platform") or "other").lower()
    if platform not in PLATFORMS:
        raise IntakeError(f"platform must be one of {sorted(PLATFORMS)}")
    published: datetime | None = data.get("published_at")
    if published is not None and published.tzinfo is None:
        published = published.replace(tzinfo=UTC)
    status = data.get("status") or "unknown"
    if status not in ("open", "closed", "unknown"):
        raise IntakeError("status must be open, closed or unknown")
    route = (data.get("contact_route") or "").strip() or None
    finding = RawFinding(
        kind=SignalKind.PARTNER_ACTIVITY if channel == "agency" else SignalKind.PROJECT_REQUEST,
        title=requirement[:200],
        source_url=url,
        original_text=excerpt,  # kept verbatim as evidence
        published_at=published,
        organization_name=(data.get("buyer_name") or "").strip() or None,
        organization_domain=None,
        provider_confidence=0.5,
        partnership_class="active_requirement" if channel == "agency" else None,
        source_status=status,
        deadline=data.get("deadline"),
        budget_text=(data.get("budget_text") or "").strip() or None,
        contact_route=route,
        routes=[RawRoute(kind=route_kind(route), value=route, source_url=url)] if route else [],
        evergreen=published is None,  # undated by the reviewer: not auto-rejected, flagged unknown
    )
    out = ingest_finding(session, finding, PROVIDER, None, settings_max_age(settings))
    opp = session.get(Opportunity, out.opportunity_id)
    assert opp is not None
    previous = apply_assessment(opp, assess_opportunity(session, opp, settings))
    session.add(
        Qualification(
            opportunity_id=opp.id,
            score=0,
            criteria={
                "intake": {
                    "by": who,
                    "at": datetime.now(UTC).isoformat(),
                    "platform": platform,
                    "channel": channel,
                    "date_supplied": published is not None,
                    "delivery_suitability": (data.get("delivery_suitability") or "").strip()
                    or None,
                    "reviewer_verification": "unverified",
                },
                "previous": previous,
            },
            rationale=(
                f"manual intake by {who} from {platform}; "
                "content is as supplied, not independently verified"
            ),
        )
    )
    session.flush()
    return opp


def settings_max_age(settings: Settings) -> int:
    return 3650  # the reviewer decides recency; undated entries are flagged, never auto-rejected
