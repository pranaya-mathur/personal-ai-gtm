import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.agents.analytics import PipelineAnalyticsAgent
from app.api.auth import Identity, require_role
from app.connectors.apify_specs import SPECS
from app.connectors.base import DiscoveryQuery
from app.connectors.registry import known_connectors
from app.core.config import get_settings
from app.db.session import get_db
from app.models import (
    AiAnalysis,
    ContactRoute,
    Evidence,
    LlmUsage,
    Opportunity,
    OpportunityEvidence,
    Organization,
    OutreachDraft,
    ProviderSpend,
    Qualification,
    SearchProfile,
    SourceCandidate,
    WorkflowRun,
)
from app.models.enums import (
    OpportunityCategory,
    OpportunityStatus,
    OutreachStatus,
    RunStatus,
    Triage,
    VerificationStatus,
)
from app.orchestrator.runner import enqueue_run
from app.schemas.api import (
    AnalysisOut,
    BoardRow,
    BudgetLine,
    CostSummary,
    DealCreate,
    DealOut,
    DealUpdate,
    DraftOut,
    DraftUpdate,
    EvidenceOut,
    IntakeIn,
    InteractionCreate,
    OpportunityDetail,
    OpportunityOut,
    ProfileCreate,
    ProfileOut,
    ProfileUpdate,
    RouteOut,
    RunCreate,
    RunOut,
)
from app.services.commercial_class import LABELS as COMMERCIAL_LABELS
from app.services.commercial_class import commercial_class
from app.services.engagement import (
    EngagementError,
    add_interaction,
    get_or_create_deal,
    mark_draft_sent,
    update_deal,
)
from app.services.engagement import list_deals as engagement_list
from app.services.fit import pipeline_stage
from app.services.intake import IntakeError, intake_lead
from app.services.project_type import project_type as project_type_of
from app.services.query_strategy import effectiveness_report
from app.services.triage import _parse_value
from app.utils import utcnow

public_router = APIRouter()
router = APIRouter(dependencies=[Depends(require_role("viewer"))])
OPERATOR = Depends(require_role("operator"))
ADMIN = Depends(require_role("admin"))


@public_router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@public_router.get("/health/ready")
def ready(db: Session = Depends(get_db)) -> dict[str, str]:
    db.execute(text("SELECT 1"))
    return {"status": "ready"}


_FREE_NOTES = {
    "public_partner_pages": "Free. Reads partner/subcontracting pages of companies listed in "
    "AGENCY_SEED_URLS (robots.txt + SSRF-safe). States-nothing pages yield no record.",
    "public_announcement_feeds": "Free. RSS/Atom feeds listed in AGENCY_FEED_URLS.",
    "wikidata_agencies": "Free open registry (CC0). Produces POTENTIAL partners only: existence, "
    "not demand.",
    "ted_api": "Free official EU TED v3 API. High volume; many notices fail small-team deal fit.",
    "worldbank_api": "Free World Bank procurement notices. Often large/complex vs preferred deal size.",
    "uk_contracts_finder": "Free UK Contracts Finder OCDS API.",
    "uk_find_a_tender": "Free UK Find a Tender OCDS API.",
    "canadabuys": "Free CanadaBuys open-data CSV of open tender notices.",
    "hn_freelancer_threads": "Free HN Algolia API; SEEKING FREELANCER comments only.",
    "sam_gov": "Free official SAM.gov Opportunities API. Requires SAMGOV_API_KEY. Off without key.",
    "intent_search": "Brave Search intent queries (billable). Off unless SEARCH_DISCOVERY_ENABLED.",
    "apify_intent_search": "Apify SERP intent search (billable). Off unless APIFY_PAID_EXECUTION_APPROVED.",
}


def _usable(name: str, spec: object | None, settings: object) -> bool:
    if spec is not None:
        return bool(spec.reviewed)  # type: ignore[attr-defined]
    if name == "public_partner_pages":
        return bool(settings.agency_seed_list)  # type: ignore[attr-defined]
    if name == "public_announcement_feeds":
        return bool(settings.agency_feed_list)  # type: ignore[attr-defined]
    if name == "sam_gov":
        key = getattr(settings, "samgov_api_key", None)
        return bool(key and key.get_secret_value())
    if name in ("intent_search",):
        return bool(
            getattr(settings, "search_discovery_enabled", False)
            and getattr(settings, "brave_api_key", None)
        )
    if name == "apify_intent_search":
        return bool(getattr(settings, "apify_paid_execution_approved", False))
    return True


@router.get("/connectors")
def connectors() -> list[dict[str, object]]:
    """All known providers with whether they are enabled, free, and approved to spend."""
    settings = get_settings()
    enabled = set(settings.enabled_connector_names)
    out: list[dict[str, object]] = []
    for name in known_connectors():
        spec = SPECS.get(name)
        out.append(
            {
                "name": name,
                "enabled": name in enabled,
                "is_mock": name.startswith("mock"),
                "paid": spec is not None,
                "usable": _usable(name, spec, settings),
                "paid_execution_approved": settings.apify_paid_execution_approved if spec else None,
                "actor_id": spec.actor_id if spec else None,
                "schema_validated_on": spec.validated_on if spec else None,
                "notes": spec.notes if spec else _FREE_NOTES.get(name, "offline fixture data"),
            }
        )
    return out


@router.get("/ai/status")
def ai_status() -> dict[str, object]:
    """What the AI layer would do right now. Never exposes secrets."""
    s = get_settings()
    key_set = bool(s.openai_api_key and s.openai_api_key.get_secret_value())
    prices = s.openai_input_usd_per_mtok is not None and s.openai_output_usd_per_mtok is not None
    live_ready = (
        not s.llm_mock_mode
        and s.llm_paid_calls_enabled
        and key_set
        and bool(s.openai_model)
        and prices
    )
    return {
        "provider": s.llm_provider,
        "mode": "mock" if s.llm_mock_mode else "live",
        "paid_calls_enabled": s.llm_paid_calls_enabled,
        "api_key_configured": key_set,
        "model": s.openai_model or None,
        "prices_configured": prices,
        "would_make_paid_calls": live_ready,
        "per_run_llm_limit_usd": s.llm_spend_limit_per_run_usd,
        "max_opportunities_per_run": s.ai_max_opportunities_per_run,
    }


@router.get("/ai/usage")
def ai_usage(db: Session = Depends(get_db)) -> dict[str, object]:
    """`calls` = real model calls (accepted + rejected); cache hits/refusals are separate."""
    real = LlmUsage.outcome.in_(["accepted", "rejected"])
    totals = db.execute(
        select(
            func.coalesce(func.sum(LlmUsage.input_tokens), 0),
            func.coalesce(func.sum(LlmUsage.output_tokens), 0),
            func.coalesce(func.sum(LlmUsage.cost_usd), 0),
            func.count().filter(real),
            func.count().filter(LlmUsage.outcome == "cached"),
            func.count().filter(LlmUsage.outcome == "failed"),
        )
    ).one()
    by_task = db.execute(
        select(LlmUsage.task, LlmUsage.outcome, func.count(), func.sum(LlmUsage.cost_usd)).group_by(
            LlmUsage.task, LlmUsage.outcome
        )
    ).all()
    by_agent: dict[str, dict[str, object]] = {}
    for row in db.execute(select(LlmUsage)).scalars():
        a = by_agent.setdefault(
            row.agent or "unattributed",
            {
                "calls": 0,
                "cached": 0,
                "failed": 0,
                "rejected": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
                "reasons": {},
            },
        )
        if row.outcome in ("accepted", "rejected"):
            a["calls"] += 1  # type: ignore[operator]
        if row.outcome in ("cached", "failed", "rejected"):
            a[row.outcome] += 1  # type: ignore[operator]
        a["input_tokens"] += row.input_tokens  # type: ignore[operator]
        a["output_tokens"] += row.output_tokens  # type: ignore[operator]
        a["cost_usd"] += float(row.cost_usd)  # type: ignore[operator]
        if row.reason:
            key = row.reason[:80]
            reasons: dict[str, int] = a["reasons"]  # type: ignore[assignment]
            reasons[key] = reasons.get(key, 0) + 1
    return {
        "input_tokens": int(totals[0]),
        "output_tokens": int(totals[1]),
        "cost_usd": float(totals[2]),
        "calls": int(totals[3]),
        "cached": int(totals[4]),
        "failed": int(totals[5]),
        "by_task": [
            {"task": t, "outcome": o, "calls": int(n), "cost_usd": float(c or 0)}
            for t, o, n, c in by_task
        ],
        "by_agent": [{"agent": k, **v} for k, v in sorted(by_agent.items())],
    }


@router.get("/opportunities/{opp_id}/analyses", response_model=list[AnalysisOut])
def opportunity_analyses(opp_id: uuid.UUID, db: Session = Depends(get_db)) -> list[AiAnalysis]:
    return list(
        db.execute(
            select(AiAnalysis)
            .where(AiAnalysis.opportunity_id == opp_id)
            .order_by(AiAnalysis.created_at)
        ).scalars()
    )


@router.get("/search-profiles", response_model=list[ProfileOut])
def list_profiles(db: Session = Depends(get_db)) -> list[SearchProfile]:
    return list(db.execute(select(SearchProfile).order_by(SearchProfile.name)).scalars())


@router.post(
    "/search-profiles", response_model=ProfileOut, status_code=201, dependencies=[OPERATOR]
)
def create_profile(body: ProfileCreate, db: Session = Depends(get_db)) -> SearchProfile:
    unknown = [c for c in body.connector_names if c not in known_connectors()]
    if unknown:
        raise HTTPException(422, f"unknown connectors: {unknown}")
    if db.execute(select(SearchProfile).where(SearchProfile.name == body.name)).first():
        raise HTTPException(409, "a profile with that name exists")
    profile = SearchProfile(**body.model_dump())
    db.add(profile)
    db.commit()
    return profile


@router.patch("/search-profiles/{profile_id}", response_model=ProfileOut, dependencies=[OPERATOR])
def update_profile(
    profile_id: uuid.UUID, body: ProfileUpdate, db: Session = Depends(get_db)
) -> SearchProfile:
    profile = db.get(SearchProfile, profile_id)
    if profile is None:
        raise HTTPException(404, "profile not found")
    for key, value in body.model_dump(exclude_none=True).items():
        setattr(profile, key, value)
    db.commit()
    return profile


@router.get("/evidence", response_model=list[EvidenceOut])
def list_evidence(run_id: uuid.UUID | None = None, db: Session = Depends(get_db)) -> list[Evidence]:
    stmt = select(Evidence).order_by(Evidence.retrieved_at.desc()).limit(200)
    if run_id:
        stmt = stmt.where(Evidence.run_id == run_id)
    return list(db.execute(stmt).scalars())


@router.post("/runs", response_model=RunOut, status_code=202, dependencies=[OPERATOR])
def create_run(body: RunCreate, db: Session = Depends(get_db)) -> WorkflowRun:
    query = body.query
    if body.profile_id is not None:
        profile = db.get(SearchProfile, body.profile_id)
        if profile is None:
            raise HTTPException(404, "profile not found")
        if not profile.enabled:
            raise HTTPException(409, "profile is disabled")
        query = DiscoveryQuery(
            keywords=profile.keywords,
            regions=profile.regions,
            max_results=profile.max_results,
            max_age_days=profile.max_age_days,
            connector_names=profile.connector_names or None,
        )
    return enqueue_run(
        db,
        body.kind,
        {
            "query": query.model_dump(),
            "profile_id": str(body.profile_id) if body.profile_id else None,
        },
    )


@router.get("/runs", response_model=list[RunOut])
def list_runs(db: Session = Depends(get_db)) -> list[WorkflowRun]:
    return list(
        db.execute(select(WorkflowRun).order_by(WorkflowRun.created_at.desc()).limit(50)).scalars()
    )


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(run_id: uuid.UUID, db: Session = Depends(get_db)) -> WorkflowRun:
    run = db.get(WorkflowRun, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    return run


@router.get("/opportunities", response_model=list[OpportunityOut])
def list_opportunities(
    category: OpportunityCategory | None = None, db: Session = Depends(get_db)
) -> list[Opportunity]:
    stmt = select(Opportunity).order_by(Opportunity.last_seen_at.desc()).limit(200)
    if category:
        stmt = stmt.where(Opportunity.category == category)
    return list(db.execute(stmt).scalars())


@router.get("/opportunities/{opp_id}", response_model=OpportunityDetail)
def get_opportunity(opp_id: uuid.UUID, db: Session = Depends(get_db)) -> OpportunityDetail:
    opp = db.get(Opportunity, opp_id)
    if opp is None:
        raise HTTPException(404, "opportunity not found")
    evs = db.execute(
        select(Evidence)
        .join(OpportunityEvidence, OpportunityEvidence.evidence_id == Evidence.id)
        .where(OpportunityEvidence.opportunity_id == opp_id)
    ).scalars()
    data = OpportunityOut.model_validate(opp).model_dump()
    return OpportunityDetail(**data, evidence=list(evs))


@router.post(
    "/opportunities/{opp_id}/verify", response_model=OpportunityOut, dependencies=[OPERATOR]
)
def verify_opportunity(
    opp_id: uuid.UUID,
    who: Identity = OPERATOR,
    db: Session = Depends(get_db),
) -> Opportunity:
    """Human verification: the only path to VERIFIED. Stale evidence cannot be verified."""
    opp = db.get(Opportunity, opp_id)
    if opp is None:
        raise HTTPException(404, "opportunity not found")
    if opp.verification_status == VerificationStatus.STALE:
        raise HTTPException(409, "stale opportunities cannot be verified; refresh the source")
    opp.verification_status = VerificationStatus.VERIFIED
    opp.verified_by = who.name
    opp.verified_at = utcnow()
    db.commit()
    return opp


@router.get("/drafts", response_model=list[DraftOut])
def list_drafts(
    status: OutreachStatus | None = None, db: Session = Depends(get_db)
) -> list[OutreachDraft]:
    stmt = select(OutreachDraft).order_by(OutreachDraft.created_at.desc()).limit(200)
    if status:
        stmt = stmt.where(OutreachDraft.status == status)
    return list(db.execute(stmt).scalars())


@router.patch("/drafts/{draft_id}", response_model=DraftOut, dependencies=[OPERATOR])
def edit_draft(
    draft_id: uuid.UUID, body: DraftUpdate, db: Session = Depends(get_db)
) -> OutreachDraft:
    draft = db.get(OutreachDraft, draft_id)
    if draft is None:
        raise HTTPException(404, "draft not found")
    if draft.status != OutreachStatus.PENDING_APPROVAL:
        raise HTTPException(409, "only pending drafts can be edited")
    draft.body = body.body
    if body.subject is not None:
        draft.subject = body.subject
    db.commit()
    return draft


def _decide(
    db: Session, draft_id: uuid.UUID, approver: str, status: OutreachStatus
) -> OutreachDraft:
    draft = db.get(OutreachDraft, draft_id)
    if draft is None:
        raise HTTPException(404, "draft not found")
    if draft.status != OutreachStatus.PENDING_APPROVAL:
        raise HTTPException(409, f"draft is {draft.status}, not pending approval")
    if status is OutreachStatus.APPROVED and "[" in draft.body:
        raise HTTPException(409, "draft still contains [PLACEHOLDER] text; edit it first")
    draft.status = status
    draft.approved_by = approver
    draft.approved_at = utcnow()
    db.commit()
    return draft


@router.post("/drafts/{draft_id}/approve", response_model=DraftOut)
def approve_draft(
    draft_id: uuid.UUID, who: Identity = ADMIN, db: Session = Depends(get_db)
) -> OutreachDraft:
    """Approval by the AUTHENTICATED admin only. There is no sender; nothing is transmitted."""
    return _decide(db, draft_id, who.name, OutreachStatus.APPROVED)


@router.post("/drafts/{draft_id}/reject", response_model=DraftOut)
def reject_draft(
    draft_id: uuid.UUID, who: Identity = OPERATOR, db: Session = Depends(get_db)
) -> OutreachDraft:
    return _decide(db, draft_id, who.name, OutreachStatus.REJECTED)


@router.get("/metrics")
def metrics(db: Session = Depends(get_db)) -> dict[str, object]:
    return PipelineAnalyticsAgent(db).run()


def view_for(opp: Opportunity, org_kind: str | None) -> str:
    """Exactly one list per record. Speculative prospects are never counted as active projects."""
    if opp.triage == Triage.REJECTED:
        return "rejected"
    cat = opp.category
    agency = opp.channel == "agency" or org_kind == "agency"
    if opp.channel == "proactive_buyer":
        return "proactive_prospect"  # a business trigger, never counted as demand
    if cat == OpportunityCategory.VERIFIED_PARTNERSHIP:
        return "verified_partnership"
    if cat == OpportunityCategory.SPECULATIVE_PROSPECT:
        return "potential_partner" if agency else "unqualified_signal"
    if agency:
        return "agency_seeking"  # a stated agency need; a human must still confirm it is current
    if opp.triage == Triage.ACTIONABLE:
        return "active_project"
    return "needs_verification"


def next_action(opp: Opportunity, score: float | None) -> str:
    """Deterministic recommendation (no LLM): what a human should do next."""
    stored = (opp.assessment or {}).get("next_action")
    if stored and opp.triage != Triage.UNASSESSED:
        if (
            opp.triage == Triage.ACTIONABLE
            and opp.verification_status != VerificationStatus.VERIFIED
        ):
            return f"{stored}. Also verify the source yourself and mark it verified."
        return str(stored)
    if opp.verification_status == VerificationStatus.STALE:
        return "Source is stale: re-check the original posting before any action"
    if opp.status == OpportunityStatus.DISQUALIFIED:
        return "Disqualified: no action"
    if opp.verification_status != VerificationStatus.VERIFIED:
        return "Open the source URL and verify the requirement, then mark it verified"
    if opp.category == OpportunityCategory.SPECULATIVE_PROSPECT:
        return "Speculative: find a source that states an actual need before outreach"
    if opp.status == OpportunityStatus.QUALIFIED:
        return "Review the pending draft; approve it yourself if it is accurate"
    if score is None:
        return "Awaiting qualification"
    return f"Review qualification (score {score})"


@router.get("/board", response_model=list[BoardRow])
def board(
    category: OpportunityCategory | None = None,
    view: str | None = None,
    triage: str | None = None,
    channel: str | None = None,
    country: str | None = None,
    project_type: str | None = None,
    min_budget: float | None = None,
    max_budget: float | None = None,
    max_age_days: int | None = None,
    include_mock: bool = True,
    db: Session = Depends(get_db),
) -> list[BoardRow]:
    """Filters apply to what the source states. A record with an unknown budget is excluded by a
    budget filter (unknown is not 'zero'); recency uses the publication date, else first seen."""
    stmt = select(Opportunity).order_by(Opportunity.last_seen_at.desc()).limit(500)
    if category:
        stmt = stmt.where(Opportunity.category == category)
    if channel:
        stmt = stmt.where(Opportunity.channel == channel)
    if triage:
        stmt = stmt.where(Opportunity.triage == triage)
    rows: list[BoardRow] = []
    now = utcnow()
    for opp in db.execute(stmt).scalars():
        org = db.get(Organization, opp.organization_id) if opp.organization_id else None
        qual = (
            db.execute(
                select(Qualification)
                .where(Qualification.opportunity_id == opp.id)
                .order_by(Qualification.created_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        evs = list(
            db.execute(
                select(Evidence)
                .join(OpportunityEvidence, OpportunityEvidence.evidence_id == Evidence.id)
                .where(OpportunityEvidence.opportunity_id == opp.id)
                .order_by(Evidence.retrieved_at)
            ).scalars()
        )
        dossier: dict[str, Any] = {}
        summary = None
        for a in db.execute(
            select(AiAnalysis)
            .where(AiAnalysis.opportunity_id == opp.id, AiAnalysis.validated.is_(True))
            .order_by(AiAnalysis.created_at.desc())
        ).scalars():
            out = a.output or {}
            if not dossier and a.task == "dossier":
                dossier = out
            if summary is None and a.task in ("dossier", "opportunity_analysis"):
                summary = out.get("summary")
        budget = opp.budget_text or ((dossier.get("budget") or {}).get("text"))
        bval, bcur = _parse_value(budget)
        pub = evs[0].published_at if evs else None
        ref = pub or opp.first_seen_at
        if ref is not None and ref.tzinfo is None:
            ref = ref.replace(tzinfo=now.tzinfo)
        age = (now - ref).days if ref is not None else None
        text = f"{opp.title}\n{evs[0].original_text if evs else ''}"
        ptype = project_type_of(text)
        routes = [
            RouteOut(
                kind=r.kind, value=r.value, role=r.role, source_url=r.source_url, snippet=r.snippet
            )
            for r in db.execute(
                select(ContactRoute).where(ContactRoute.opportunity_id == opp.id)
            ).scalars()
        ]
        intake_meta = next(
            (
                q.criteria["intake"]
                for q in db.execute(
                    select(Qualification).where(Qualification.opportunity_id == opp.id)
                ).scalars()
                if isinstance(q.criteria, dict) and "intake" in q.criteria
            ),
            None,
        )
        score = qual.score if qual else None
        is_mock = bool(evs) and all(e.provider.startswith("mock") for e in evs)
        row = BoardRow(
            id=opp.id,
            category=opp.category,
            title=opp.title,
            status=opp.status,
            verification_status=opp.verification_status,
            confidence=opp.confidence,
            organization=org.name if org else None,
            organization_kind=org.kind if org else None,
            country=org.country if org else None,
            domain=org.domain if org else None,
            is_mock=is_mock,
            score=score,
            qualification_rationale=qual.rationale if qual else None,
            evidence_count=len(evs),
            source_url=evs[0].source_url if evs else None,
            published_at=pub,
            stated_budget=budget,
            ai_summary=summary,
            next_action=next_action(opp, score),
            last_seen_at=opp.last_seen_at,
            triage=opp.triage,
            technical_relevance=opp.technical_relevance,
            rejection_reason=opp.rejection_reason,
            source_status=opp.source_status,
            deadline=opp.deadline,
            contact_route=opp.contact_route,
            missing=list((opp.assessment or {}).get("missing", [])),
            reasons=list((opp.assessment or {}).get("reasons", [])),
            stage_a=dict((opp.assessment or {}).get("stage_a", {})),
            stage_b=dict((opp.assessment or {}).get("stage_b", {})),
            view=view_for(opp, org.kind if org else None),
            evidence_excerpt=(evs[0].original_text[:300] if evs else None),
            channel=opp.channel,
            project_type=ptype,
            budget_value=bval,
            budget_currency=bcur or None,
            age_days=age,
            routes=routes,
            ai_next_action=dossier.get("suggested_next_action"),
            small_team_fit=dossier.get("small_team_fit"),
            external_delivery=dossier.get("external_delivery"),
            pipeline_stage=pipeline_stage(db, opp),
            origin="manual" if evs and all(e.provider == "manual_intake" for e in evs) else "auto",
            intake=intake_meta,
            commercial_class=commercial_class(
                category=opp.category, triage=opp.triage, channel=opp.channel
            ),
        )
        if is_mock and not include_mock:
            continue
        if view and row.view != view:
            continue
        if country and country.lower() not in (row.country or "").lower():
            continue
        if project_type and row.project_type != project_type:
            continue
        if (min_budget is not None or max_budget is not None) and (
            row.budget_value is None
            or (min_budget is not None and row.budget_value < min_budget)
            or (max_budget is not None and row.budget_value > max_budget)
        ):
            continue
        if max_age_days is not None and (row.age_days is None or row.age_days > max_age_days):
            continue
        rows.append(row)
    return rows


@router.get("/routes")
def contact_routes(db: Session = Depends(get_db)) -> list[dict[str, object]]:
    """Every published contact route with the evidence it came from and the next step.
    Routes are exactly what the source printed; none are constructed or purchased."""
    out: list[dict[str, object]] = []
    for r in db.execute(
        select(ContactRoute).order_by(ContactRoute.created_at.desc()).limit(500)
    ).scalars():
        opp = db.get(Opportunity, r.opportunity_id)
        if opp is None:
            continue
        org = db.get(Organization, r.organization_id) if r.organization_id else None
        out.append(
            {
                "kind": r.kind,
                "value": r.value,
                "role": r.role,
                "source_url": r.source_url,
                "opportunity_id": str(opp.id),
                "opportunity": opp.title,
                "organization": org.name if org else None,
                "country": org.country if org else None,
                "channel": opp.channel,
                "triage": opp.triage,
                "view": view_for(opp, org.kind if org else None),
                "next_action": next_action(opp, None),
                "is_mock": r.source_url.startswith("https://mock") or ".example" in r.source_url,
            }
        )
    return out


@router.get("/sources/performance")
def source_performance(db: Session = Depends(get_db)) -> dict[str, object]:
    """Per-source yield, built only from stored records: what each source produced and how much
    of it survived triage. 'false_positive' = rejected as not relevant after being retrieved."""
    providers = [p for (p,) in db.execute(select(Evidence.provider).distinct()).all()]
    run_totals: dict[str, dict[str, Any]] = {}
    for st in db.execute(select(WorkflowRun.state)).scalars():
        for cs in (st or {}).get("connector_stats", []):
            a = run_totals.setdefault(cs["connector"], {"retrieved": 0, "duplicates": 0, "runs": 0})
            a["retrieved"] += cs.get("retrieved", 0)
            a["duplicates"] += cs.get("duplicates", 0)
            a["runs"] += 1
            if cs.get("status") in ("failed", "blocked"):
                a["last_error"] = cs.get("status")
    for name in run_totals:  # sources that only ever returned nothing still appear
        if name not in providers:
            providers.append(name)
    sources: list[dict[str, object]] = []
    for prov in sorted(providers):
        ids = [
            i
            for (i,) in db.execute(
                select(OpportunityEvidence.opportunity_id)
                .join(Evidence, Evidence.id == OpportunityEvidence.evidence_id)
                .where(Evidence.provider == prov)
                .distinct()
            ).all()
        ]
        opps = list(db.execute(select(Opportunity).where(Opportunity.id.in_(ids))).scalars())
        with_route = (
            db.execute(
                select(func.count(func.distinct(ContactRoute.opportunity_id))).where(
                    ContactRoute.opportunity_id.in_(ids)
                )
            ).scalar_one()
            if ids
            else 0
        )
        spend = db.execute(
            select(func.coalesce(func.sum(ProviderSpend.amount_usd), 0)).where(
                ProviderSpend.provider.in_([prov, f"apify:{prov}", f"brave:{prov}"])
            )
        ).scalar_one()
        views = []
        ages: list[int] = []
        routed = (
            {
                i
                for (i,) in db.execute(
                    select(ContactRoute.opportunity_id).where(ContactRoute.opportunity_id.in_(ids))
                ).all()
            }
            if ids
            else set()
        )
        now_ = utcnow()
        for o in opps:
            org_ = db.get(Organization, o.organization_id) if o.organization_id else None
            views.append((o, view_for(o, org_.kind if org_ else None)))
            ref = (
                o.first_seen_at
                if o.first_seen_at.tzinfo
                else o.first_seen_at.replace(tzinfo=now_.tzinfo)
            )
            ages.append((now_ - ref).days)
        signals = [o for o, v in views if v in ("active_project", "agency_seeking")]
        usable = [o for o in signals if o.id in routed and o.verification_status != "stale"]
        cost_total = float(spend)
        agg = run_totals.get(prov, {})
        sources.append(
            {
                "source": prov,
                "retrieved_total": agg.get("retrieved", 0),
                "duplicates_total": agg.get("duplicates", 0),
                "runs": agg.get("runs", 0),
                "last_error": agg.get("last_error"),
                "demand_signals": len(signals),
                "usable_opportunities": len(usable),
                "cost_per_usable_usd": (cost_total / len(usable)) if usable else None,
                "median_age_days": sorted(ages)[len(ages) // 2] if ages else None,
                "channels": {
                    "agency": sum(o.channel == "agency" for o in opps),
                    "direct_buyer": sum(o.channel == "direct_buyer" for o in opps),
                },
                "is_mock": prov.startswith("mock"),
                "records": len(opps),
                "actionable": sum(o.triage == Triage.ACTIONABLE for o in opps),
                "needs_verification": sum(o.triage == Triage.NEEDS_VERIFICATION for o in opps),
                "rejected": sum(o.triage == Triage.REJECTED for o in opps),
                "false_positives": sum(
                    o.triage == Triage.REJECTED and "not_relevant" in (o.rejection_reason or "")
                    for o in opps
                ),
                "expired_or_awarded": sum(
                    o.triage == Triage.REJECTED
                    and any(k in (o.rejection_reason or "") for k in ("closed", "awarded"))
                    for o in opps
                ),
                "missing_contact_route": len(opps) - int(with_route),
                "cost_usd": float(spend),
            }
        )
    cands = db.execute(
        select(SourceCandidate.origin, SourceCandidate.status, func.count()).group_by(
            SourceCandidate.origin, SourceCandidate.status
        )
    ).all()
    candidates: dict[str, dict[str, int]] = {}
    for origin, status, n in cands:
        candidates.setdefault(origin, {})[status] = int(n)
    return {"sources": sources, "candidates": candidates}


@router.post("/runs/{run_id}/cancel", response_model=RunOut, dependencies=[OPERATOR])
def cancel_run(run_id: uuid.UUID, db: Session = Depends(get_db)) -> WorkflowRun:
    """Cancels a queued or running job. A running job stops at the next node boundary."""
    run = db.get(WorkflowRun, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    if run.status in (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED):
        raise HTTPException(409, f"run already {run.status}")
    run.status = RunStatus.CANCELLED
    run.finished_at = utcnow()
    db.commit()
    return run


@router.get("/costs", response_model=CostSummary)
def costs(db: Session = Depends(get_db)) -> CostSummary:
    s = get_settings()

    def total(prefix: str | None = None, since: datetime | None = None) -> float:
        stmt = select(func.coalesce(func.sum(ProviderSpend.amount_usd), 0))
        if prefix:
            stmt = stmt.where(ProviderSpend.provider.like(f"{prefix}%"))
        if since:
            stmt = stmt.where(ProviderSpend.created_at >= since)
        return float(db.execute(stmt).scalar_one())

    now = utcnow()
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    lines = []
    for family, cap in (("openai", s.openai_test_budget_usd), ("apify", s.apify_test_budget_usd)):
        spent = total(family)
        lines.append(
            BudgetLine(
                family=family,
                spent_usd=spent,
                cap_usd=cap,
                remaining_usd=None if cap is None else max(0.0, cap - spent),
            )
        )
    return CostSummary(
        lines=lines,
        total_spent_usd=total(),
        total_cap_usd=s.total_test_budget_usd,
        today_usd=total(since=day),
        month_usd=total(since=day.replace(day=1)),
        note=(
            "Estimated from token usage and Actor-reported charges. Provider billing can lag and "
            "an in-flight call can overshoot a cap by up to its own cost; reconcile in the "
            "provider consoles."
        ),
    )


@router.post("/intake", response_model=BoardRow, status_code=201, dependencies=[OPERATOR])
def create_intake(
    body: IntakeIn, who: Identity = OPERATOR, db: Session = Depends(get_db)
) -> BoardRow:
    """Add one human-reviewed lead. Stored separately (origin=manual); nothing is fetched."""
    try:
        opp = intake_lead(db, get_settings(), who.name, body.model_dump())
    except IntakeError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return next(r for r in board(include_mock=True, db=db) if r.id == opp.id)


@router.get("/queries/performance")
def query_performance(db: Session = Depends(get_db)) -> dict[str, object]:
    """Effectiveness of intent-search queries. Empty until a gated search provider has run."""
    return {"queries": effectiveness_report(db), "commercial_class_labels": COMMERCIAL_LABELS}


@router.get("/deals", response_model=list[DealOut])
def deals(follow_ups: bool = False, db: Session = Depends(get_db)) -> list[dict[str, object]]:
    return engagement_list(db, follow_ups_only=follow_ups)


@router.post("/deals", response_model=DealOut, status_code=201, dependencies=[OPERATOR])
def create_deal(body: DealCreate, db: Session = Depends(get_db)) -> dict[str, object]:
    try:
        deal = get_or_create_deal(db, body.opportunity_id)
    except EngagementError as exc:
        raise HTTPException(404, str(exc)) from exc
    db.commit()
    return next(d for d in engagement_list(db) if d["id"] == deal.id)


@router.patch("/deals/{deal_id}", response_model=DealOut, dependencies=[OPERATOR])
def patch_deal(
    deal_id: uuid.UUID, body: DealUpdate, db: Session = Depends(get_db)
) -> dict[str, object]:
    try:
        update_deal(
            db,
            deal_id,
            stage=body.stage,
            value_usd=body.value_usd,
            notes=body.notes,
            next_follow_up_at=body.next_follow_up_at,
            clear_follow_up=body.clear_follow_up,
        )
    except EngagementError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return next(d for d in engagement_list(db) if d["id"] == deal_id)


@router.post(
    "/deals/{deal_id}/interactions",
    response_model=DealOut,
    status_code=201,
    dependencies=[OPERATOR],
)
def create_interaction(
    deal_id: uuid.UUID, body: InteractionCreate, db: Session = Depends(get_db)
) -> dict[str, object]:
    try:
        add_interaction(
            db, deal_id, kind=body.kind, summary=body.summary, occurred_at=body.occurred_at
        )
    except EngagementError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return next(d for d in engagement_list(db) if d["id"] == deal_id)


@router.post("/drafts/{draft_id}/mark-sent", response_model=DraftOut, dependencies=[OPERATOR])
def draft_mark_sent(
    draft_id: uuid.UUID, who: Identity = OPERATOR, db: Session = Depends(get_db)
) -> OutreachDraft:
    """Record that you manually sent an approved draft. Does not transmit anything."""
    try:
        draft = mark_draft_sent(db, draft_id, who.name)
    except EngagementError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return draft
