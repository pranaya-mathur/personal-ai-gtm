"""Commercial-intent classes, query strategy, ranking, SAM parse, engagement."""

from datetime import UTC, datetime
from uuid import uuid4

from app.connectors.sam_gov import SamGovConnector
from app.core.config import Settings
from app.models import Deal, Opportunity, Organization
from app.models.enums import (
    DealStage,
    OpportunityCategory,
    OpportunityStatus,
    OrganizationKind,
    Triage,
)
from app.services.commercial_class import (
    ACTIVE_AGENCY_PARTNER_NEED,
    CONFIRMED_ACTIVE_REQUEST,
    POTENTIAL_NO_DEMAND,
    REJECTED,
    VERIFIED_AGENCY_MODEL,
    commercial_class,
)
from app.services.engagement import add_interaction, get_or_create_deal, update_deal
from app.services.query_strategy import build_plan, fingerprint
from app.services.ranking import score_agency, score_buyer, score_opportunity


def test_commercial_class_mapping() -> None:
    assert (
        commercial_class(
            category=OpportunityCategory.CONFIRMED_DEMAND,
            triage=Triage.ACTIONABLE,
            channel="direct_buyer",
        )
        == CONFIRMED_ACTIVE_REQUEST
    )
    assert (
        commercial_class(
            category=OpportunityCategory.CONFIRMED_DEMAND,
            triage=Triage.NEEDS_VERIFICATION,
            channel="agency",
        )
        == ACTIVE_AGENCY_PARTNER_NEED
    )
    assert (
        commercial_class(
            category=OpportunityCategory.VERIFIED_PARTNERSHIP,
            triage=Triage.NEEDS_VERIFICATION,
            channel="agency",
        )
        == VERIFIED_AGENCY_MODEL
    )
    assert (
        commercial_class(
            category=OpportunityCategory.SPECULATIVE_PROSPECT,
            triage=Triage.NEEDS_VERIFICATION,
            channel="agency",
        )
        == POTENTIAL_NO_DEMAND
    )
    assert (
        commercial_class(
            category=OpportunityCategory.CONFIRMED_DEMAND,
            triage=Triage.REJECTED,
            channel="direct_buyer",
        )
        == REJECTED
    )


def test_query_plan_expands_and_dedupes() -> None:
    s = Settings(
        intent_queries_agency='"looking for" "{facet}" partner agency',
        intent_queries_buyer='"RFP" "{facet}" implementation',
        query_plan_max_per_channel=6,
    )
    plan = build_plan(s)
    assert any(p.origin == "configured" for p in plan)
    assert any(p.origin == "expanded" and "RAG" in p.text for p in plan)
    fps = [p.fingerprint for p in plan]
    assert len(fps) == len(set(fps))
    assert fingerprint("  Hello  World ") == fingerprint("hello world")


def test_buyer_rank_prefers_actionable_over_speculative() -> None:
    research = {
        "all_fresh": True,
        "has_publication_date": True,
        "has_source_url": True,
        "known_contacts": 1,
        "has_contact_route": True,
        "source_confirmed": True,
    }
    active, _ = score_buyer(
        category=OpportunityCategory.CONFIRMED_DEMAND,
        triage=Triage.ACTIONABLE,
        confidence=0.8,
        research=research,
        deal_size_result="pass",
        age_days=5,
    )
    speculative, _ = score_buyer(
        category=OpportunityCategory.SPECULATIVE_PROSPECT,
        triage=Triage.NEEDS_VERIFICATION,
        confidence=0.9,
        research=research,
        age_days=5,
    )
    assert active > speculative


def test_agency_rank_model_below_active_need() -> None:
    research = {
        "all_fresh": True,
        "has_publication_date": True,
        "has_source_url": True,
        "has_contact_route": True,
    }
    need, _ = score_agency(
        category=OpportunityCategory.CONFIRMED_DEMAND,
        triage=Triage.NEEDS_VERIFICATION,
        confidence=0.7,
        research=research,
    )
    model, _ = score_agency(
        category=OpportunityCategory.VERIFIED_PARTNERSHIP,
        triage=Triage.NEEDS_VERIFICATION,
        confidence=0.7,
        research=research,
    )
    assert need > model
    score, meta = score_opportunity(
        channel="agency",
        category=OpportunityCategory.VERIFIED_PARTNERSHIP,
        triage=Triage.NEEDS_VERIFICATION,
        confidence=0.7,
        research=research,
    )
    assert meta["rubric"] == "agency_partnership"
    assert meta["commercial_class"] == VERIFIED_AGENCY_MODEL
    assert score == model


def test_sam_gov_parse_filters_and_status() -> None:
    payload = {
        "opportunitiesData": [
            {
                "title": "Generative AI chatbot development services",
                "noticeId": "abc123",
                "description": "Build a RAG assistant for citizen services",
                "active": "Yes",
                "responseDeadLine": "12/31/2099 23:59:59",
                "postedDate": "2026-10-01",
                "fullParentPathName": "DEPARTMENT.Agency Name",
                "uiLink": "https://sam.gov/opp/abc123/view",
                "pointOfContact": [{"email": "buyer@agency.gov"}],
            },
            {
                "title": "Office furniture supply",
                "noticeId": "xyz",
                "description": "Desks and chairs",
                "active": "Yes",
                "postedDate": "2026-10-01",
            },
        ]
    }
    terms = ["ai", "rag", "chatbot", "generative"]
    found = SamGovConnector.parse(payload, terms)
    assert len(found) == 1
    assert found[0].organization_country == "USA"
    assert found[0].source_status == "open"
    assert found[0].contact_route == "buyer@agency.gov"
    assert found[0].deadline is not None


def test_engagement_pipeline(session) -> None:  # type: ignore[no-untyped-def]
    org = Organization(name="Buyer Co", kind=OrganizationKind.BUYER, domain="buyer.example")
    session.add(org)
    session.flush()
    opp = Opportunity(
        category=OpportunityCategory.CONFIRMED_DEMAND,
        title="Build AI agent",
        organization_id=org.id,
        status=OpportunityStatus.QUALIFIED,
        confidence=0.7,
        dedupe_key=str(uuid4()),
        triage=Triage.ACTIONABLE,
        channel="direct_buyer",
    )
    session.add(opp)
    session.flush()
    deal = get_or_create_deal(session, opp.id)
    assert deal.stage == DealStage.IDENTIFIED
    add_interaction(session, deal.id, kind="email_sent", summary="Sent intro")
    session.refresh(deal)
    assert deal.stage == DealStage.CONTACTED
    update_deal(session, deal.id, stage="proposal", next_follow_up_at=datetime.now(UTC))
    session.refresh(deal)
    assert deal.stage == DealStage.PROPOSAL
    session.refresh(opp)
    assert opp.status == OpportunityStatus.ENGAGED
    again = get_or_create_deal(session, opp.id)
    assert again.id == deal.id
    assert session.get(Deal, deal.id) is not None
