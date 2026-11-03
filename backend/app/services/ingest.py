import uuid
from dataclasses import dataclass
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.base import RawFinding, SignalKind
from app.core.logging import get_logger
from app.models import (
    Contact,
    ContactRoute,
    Evidence,
    Opportunity,
    OpportunityEvidence,
    Organization,
)
from app.models.enums import (
    OpportunityCategory,
    OpportunityStatus,
    OrganizationKind,
    VerificationStatus,
)
from app.services.dedupe import (
    evidence_hash,
    is_fresh,
    opportunity_dedupe_key,
    title_similarity,
)
from app.services.routes import route_kind
from app.utils import utcnow

log = get_logger(__name__)
NEAR_DUPLICATE_THRESHOLD = 0.8


def classify(finding: RawFinding) -> OpportunityCategory:
    """Strict bucket assignment. A 'confirmed demand' needs a stated need plus original text
    and a source URL; anything weaker falls to speculative rather than being promoted."""
    has_proof = bool(finding.original_text.strip()) and bool(finding.source_url.strip())
    if finding.kind is SignalKind.PROJECT_REQUEST and has_proof:
        return OpportunityCategory.CONFIRMED_DEMAND
    if finding.kind is SignalKind.PARTNER_ACTIVITY and has_proof:
        if finding.partnership_class == "active_requirement":
            return OpportunityCategory.CONFIRMED_DEMAND  # agency states a delivery need
        if finding.partnership_class == "potential":
            return OpportunityCategory.SPECULATIVE_PROSPECT  # no demand/model evidence
        return OpportunityCategory.VERIFIED_PARTNERSHIP
    return OpportunityCategory.SPECULATIVE_PROSPECT


@dataclass
class IngestOutcome:
    opportunity_id: uuid.UUID
    created: bool
    stale: bool
    new_evidence: bool
    duplicate: bool = False


def _upsert_org(
    session: Session, finding: RawFinding, kind: OrganizationKind
) -> Organization | None:
    if not finding.organization_name:
        return None
    stmt = select(Organization)
    if finding.organization_domain:
        stmt = stmt.where(Organization.domain == finding.organization_domain.lower())
    else:
        stmt = stmt.where(Organization.name == finding.organization_name)
    org = session.execute(stmt).scalars().first()
    if org is None:
        org = Organization(
            name=finding.organization_name,
            domain=finding.organization_domain.lower() if finding.organization_domain else None,
            kind=kind,
            country=finding.organization_country,
        )
        session.add(org)
        session.flush()
    return org


def validate_finding(finding: RawFinding) -> str | None:
    """Reject records that cannot be traced to a real, citable source. Returns the reason, or
    None if acceptable. A provider error or empty record must never become an opportunity."""
    if not finding.title.strip():
        return "missing title"
    if not finding.original_text.strip():
        return "missing source text"
    parsed = urlparse(finding.source_url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return "missing or invalid source URL"
    return None


def _store_routes(
    session: Session, opp: Opportunity, org: Organization | None, finding: RawFinding
) -> None:
    """Persist published routes once each. The finding's own source page is not a route."""
    candidates: list[tuple[str, str, str | None, str | None]] = (
        [(route_kind(finding.contact_route), finding.contact_route, None, None)]
        if (
            finding.contact_route
            and finding.contact_route.rstrip("/") != finding.source_url.rstrip("/")
        )
        else []
    )
    candidates += [(r.kind, r.value, r.role, r.snippet) for r in finding.routes]
    for kind, value, role, snippet in candidates:
        exists = session.execute(
            select(ContactRoute.id).where(
                ContactRoute.opportunity_id == opp.id,
                ContactRoute.kind == kind,
                ContactRoute.value == value,
            )
        ).first()
        if exists is None:
            session.add(
                ContactRoute(
                    opportunity_id=opp.id,
                    organization_id=org.id if org else None,
                    kind=kind,
                    value=value,
                    role=role,
                    source_url=finding.source_url,
                    snippet=snippet,
                )
            )


def ingest_finding(
    session: Session,
    finding: RawFinding,
    provider: str,
    run_id: uuid.UUID | None,
    max_age_days: int,
) -> IngestOutcome:
    category = classify(finding)
    fresh = finding.evergreen or is_fresh(finding.published_at, max_age_days)
    org_kind = (
        OrganizationKind.AGENCY
        if finding.kind is SignalKind.PARTNER_ACTIVITY
        else OrganizationKind.BUYER
    )
    org = _upsert_org(session, finding, org_kind)

    ev_hash = evidence_hash(finding.source_url, finding.original_text)
    evidence = session.execute(
        select(Evidence).where(Evidence.content_hash == ev_hash)
    ).scalar_one_or_none()
    new_evidence = evidence is None
    if evidence is None:
        evidence = Evidence(
            provider=provider,
            source_url=finding.source_url,
            original_text=finding.original_text,
            published_at=finding.published_at,
            confidence=finding.provider_confidence,
            verification_status=(
                VerificationStatus.UNVERIFIED if fresh else VerificationStatus.STALE
            ),
            content_hash=ev_hash,
            run_id=run_id,
        )
        session.add(evidence)
        session.flush()

    org_key = (
        (org.domain or org.name)
        if org
        else (finding.organization_domain or urlparse(finding.source_url).hostname)
    )
    key = opportunity_dedupe_key(category, org_key, finding.title)
    opp = session.execute(
        select(Opportunity).where(Opportunity.dedupe_key == key)
    ).scalar_one_or_none()
    if opp is None and org is not None:
        # near-duplicate: same org + category, almost identical title (e.g. reposted/reworded)
        for cand in session.execute(
            select(Opportunity).where(
                Opportunity.organization_id == org.id, Opportunity.category == category
            )
        ).scalars():
            if title_similarity(cand.title, finding.title) >= NEAR_DUPLICATE_THRESHOLD:
                opp = cand
                break
    created = opp is None
    if opp is None:
        opp = Opportunity(
            category=category,
            title=finding.title,
            organization_id=org.id if org else None,
            confidence=finding.provider_confidence,
            dedupe_key=key,
            channel="agency"
            if finding.kind is SignalKind.PARTNER_ACTIVITY or org_kind == OrganizationKind.AGENCY
            else "direct_buyer",
            source_status=finding.source_status,
            deadline=finding.deadline,
            budget_text=finding.budget_text,
            contact_route=finding.contact_route,
            verification_status=(
                VerificationStatus.UNVERIFIED if fresh else VerificationStatus.STALE
            ),
        )
        session.add(opp)
        session.flush()
    else:
        opp.last_seen_at = utcnow()
        if finding.source_status != "unknown":
            opp.source_status = finding.source_status
        opp.deadline = opp.deadline or finding.deadline
        opp.budget_text = opp.budget_text or finding.budget_text
        opp.contact_route = opp.contact_route or finding.contact_route
        opp.confidence = max(opp.confidence, finding.provider_confidence)
        if fresh and opp.verification_status == VerificationStatus.STALE:
            opp.verification_status = VerificationStatus.UNVERIFIED
    if not fresh and opp.status == OpportunityStatus.NEW and created:
        opp.verification_status = VerificationStatus.STALE

    linked = session.get(OpportunityEvidence, (opp.id, evidence.id))
    if linked is None:
        session.add(OpportunityEvidence(opportunity_id=opp.id, evidence_id=evidence.id))

    _store_routes(session, opp, org, finding)
    if org is not None:
        for c in finding.contacts:
            exists = session.execute(
                select(Contact).where(
                    Contact.organization_id == org.id, Contact.full_name == c.full_name
                )
            ).scalar_one_or_none()
            if exists is None:
                session.add(
                    Contact(
                        organization_id=org.id,
                        full_name=c.full_name,
                        title=c.title,
                        email=c.email,
                        profile_url=c.profile_url,
                        evidence_id=evidence.id,
                    )
                )
    session.flush()
    log.info("finding_ingested", provider=provider, created=created, stale=not fresh)
    return IngestOutcome(opp.id, created, not fresh, new_evidence, duplicate=not created)
