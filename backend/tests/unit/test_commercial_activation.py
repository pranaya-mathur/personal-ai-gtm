# ruff: noqa: E501
"""Capability matching, fit/approach, pipeline stages, proposals, onboarding, proactive triggers."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.connectors.base import RawFinding, SignalKind
from app.connectors.triggers import HnHiringTriggerConnector, TedPlanningNoticeConnector
from app.core.config import Settings
from app.models import Deal, Interaction, Opportunity, OutreachDraft
from app.models.enums import DealStage
from app.services import capabilities as caps
from app.services.engagement import add_interaction, get_or_create_deal, update_deal
from app.services.fit import opportunity_fit, outreach_from_fit, pipeline_stage
from app.services.ingest import ingest_finding
from app.services.proposals import (
    ProposalError,
    approve_proposal,
    edit_proposal,
    ensure_onboarding,
    mark_sent,
    prepare_proposal,
    toggle_onboarding,
)
from app.services.triage import apply_assessment, assess_opportunity

S = Settings(database_url="sqlite://")
NOW = datetime.now(UTC)
FIX = Path(__file__).parents[1] / "fixtures"


def make_request(session, **kw):  # type: ignore[no-untyped-def]
    f = RawFinding(
        kind=SignalKind.PROJECT_REQUEST, title="RAG assistant for support knowledge base",
        source_url="https://buyer.example/rfp/1",
        original_text="We are looking for a team to design and deploy a retrieval-augmented assistant over our support documents. Budget 8000 USD.",
        published_at=NOW, organization_name="Buyer One", organization_country="DE",
        source_status="open", deadline=NOW + timedelta(days=14), budget_text="8000 USD",
        contact_route="procurement@buyer.example",
    )  # fmt: skip
    f = f.model_copy(update=kw)
    opp = session.get(Opportunity, ingest_finding(session, f, "ted_api", None, 90).opportunity_id)
    apply_assessment(opp, assess_opportunity(session, opp, S))
    return opp


# ------------------------------------------------------------------ capabilities
def test_default_profile_has_the_owners_areas_and_no_invented_projects(session) -> None:  # type: ignore[no-untyped-def]
    areas = caps.get_profile(session)
    assert {a["key"] for a in areas} >= {
        "agentic_ai",
        "rag",
        "document_intelligence",
        "workflow_automation",
        "custom_ai_engineering",
        "cloud_integration",
        "ai_governance",
    }
    assert all(a["projects"] == [] for a in areas)  # nothing is claimed as a delivered project


def test_a_project_reference_needs_a_verifiable_link(session) -> None:  # type: ignore[no-untyped-def]
    areas = caps.get_profile(session)
    with pytest.raises(ValueError, match="verifiable_reference"):
        caps.save_profile(session, [{**areas[0], "projects": [{"title": "Bank bot"}]}], "alice")
    saved = caps.save_profile(
        session,
        [
            {
                **areas[1],
                "projects": [
                    {"title": "Support RAG", "verifiable_reference": "https://example.org/case"}
                ],
            }
        ],
        "alice",
    )
    m = caps.match_capabilities("retrieval-augmented assistant", saved)
    assert m and "1 verified project" in m[0]["evidence_status"]


def test_matching_labels_unverified_areas_as_claims() -> None:
    m = caps.match_capabilities("Build a RAG chatbot over our knowledge base", caps.DEFAULT_AREAS)
    assert m and all("NO verified project reference" in x["evidence_status"] for x in m)
    assert caps.match_capabilities("Supply of office chairs", caps.DEFAULT_AREAS) == []


# ------------------------------------------------------------------ fit and approach
def test_fit_quotes_the_source_lists_unknowns_and_never_invents_experience(session) -> None:  # type: ignore[no-untyped-def]
    opp = make_request(session)
    fit = opportunity_fit(session, opp)
    assert fit["commercial_class"] == "confirmed_active_ai_request" and fit["verdict"] == "pursue"
    assert (
        fit["evidence"][0]["url"] == "https://buyer.example/rfp/1"
        and "retrieval-augmented" in fit["evidence"][0]["quote"]
    )
    assert (
        fit["matched_capabilities"]
        and "claimed" in fit["matched_capabilities"][0]["evidence_status"]
    )
    assert fit["what_i_could_deliver"] and fit["approach"]["next_action"].startswith(
        "Verify the source yourself"
    )
    assert any("experience" in c.lower() for c in fit["approach"]["cautions"])
    subject, body = outreach_from_fit(fit, None)
    assert (
        "[YOUR RELEVANT EXPERIENCE" in body
        and "[RECIPIENT NAME" in body
        and "[PROPOSED NEXT STEP]" in body
    )
    assert (
        subject == "Re: RAG assistant for support knowledge base"
        and "Buyer One" in fit["organization"]
    )


def test_rejected_records_get_a_skip_verdict(session) -> None:  # type: ignore[no-untyped-def]
    opp = make_request(session, source_status="closed", deadline=NOW - timedelta(days=2))
    assert opportunity_fit(session, opp)["verdict"] == "skip"


# ------------------------------------------------------------------ pipeline stages
def test_stage_progresses_from_stored_facts_only(session) -> None:  # type: ignore[no-untyped-def]
    opp = make_request(session)
    assert pipeline_stage(session, opp) == "qualified"
    d = OutreachDraft(opportunity_id=opp.id, subject="s", body="b", status="pending_approval")
    session.add(d)
    session.flush()
    assert pipeline_stage(session, opp) == "outreach_prepared"
    d.status, d.approved_by, d.approved_at = "approved", "alice", NOW
    assert pipeline_stage(session, opp) == "approved"
    deal = get_or_create_deal(session, opp.id)
    add_interaction(session, deal.id, kind="email_sent", summary="sent by me")
    assert pipeline_stage(session, opp) == "contacted"
    add_interaction(session, deal.id, kind="email_received", summary="they replied")
    assert pipeline_stage(session, opp) == "responded"
    add_interaction(session, deal.id, kind="meeting", summary="intro call")
    assert pipeline_stage(session, opp) == "meeting"
    update_deal(session, deal.id, stage="proposal")
    assert pipeline_stage(session, opp) == "proposal"
    update_deal(session, deal.id, stage="negotiation")
    assert pipeline_stage(session, opp) == "negotiation"
    update_deal(session, deal.id, stage="won")
    assert pipeline_stage(session, opp) == "won"
    ensure_onboarding(session, deal.id)
    assert pipeline_stage(session, opp) == "onboarding"
    assert session.execute(select(Interaction)).scalars().all()


# ------------------------------------------------------------------ proposals + onboarding
def test_proposal_is_a_draft_with_placeholders_and_cannot_be_approved_until_resolved(
    session,
) -> None:  # type: ignore[no-untyped-def]
    opp = make_request(session)
    p = prepare_proposal(session, opp, "olga")
    assert (
        p.status == "draft"
        and "[PRICE" in p.body
        and "[RELEVANT EXPERIENCE" in p.body
        and "[TIMELINE" in p.body
    )
    assert (
        "Client-stated budget: 8000 USD" in p.body
    )  # the stated budget is quoted, not decided for the owner
    with pytest.raises(ProposalError, match="PLACEHOLDER"):
        approve_proposal(session, p.id, "alice")
    with pytest.raises(ProposalError, match="approved"):
        mark_sent(session, p.id)  # cannot be sent before approval
    edit_proposal(
        session,
        p.id,
        p.body.replace("[PRICE - set by the owner; no figure is suggested here]", "USD 7,500 fixed")
        .replace("[TIMELINE - to be estimated by the owner after discovery]", "4 weeks")
        .replace(
            "[RELEVANT EXPERIENCE - none on file. Add approved, verifiable references; do not invent]",
            "References available on request.",
        ),
        "USD 7,500",
    )
    approved = approve_proposal(session, p.id, "alice")
    assert approved.approved_by == "alice" and approved.status == "approved"
    assert mark_sent(session, p.id).status == "sent"
    with pytest.raises(ProposalError):
        edit_proposal(session, p.id, "changed", None)  # no edits after approval


def test_onboarding_checklist_is_human_ticked_and_idempotent(session) -> None:  # type: ignore[no-untyped-def]
    opp = make_request(session)
    deal = get_or_create_deal(session, opp.id)
    items = ensure_onboarding(session, deal.id)
    assert len(items) == len(ensure_onboarding(session, deal.id)) and not any(i.done for i in items)
    done = toggle_onboarding(session, items[0].id, True, "olga")
    assert done.done and done.done_by == "olga"
    assert not toggle_onboarding(session, items[0].id, False, "olga").done
    assert session.execute(select(Deal)).scalar_one().stage == DealStage.IDENTIFIED


# ------------------------------------------------------------------ proactive buyers (Pipeline C)
def test_hn_hiring_triggers_need_an_outside_help_signal_and_are_never_demand(session) -> None:  # type: ignore[no-untyped-def]
    thread = {"children": [
        {"id": 11, "created_at": "2026-11-02T10:00:00Z", "text": "Acme Robotics | ML contractor | Berlin | REMOTE<p>We are building a new in-house AI team and use contractors. Email jane@acme.example (Head of AI). Apply: https://acme.example/careers"},
        {"id": 12, "created_at": "2026-11-02T10:00:00Z", "text": "Plain Corp | Machine Learning Engineer | NYC | ONSITE | Full-time<p>Great benefits. jobs@plain.example"},
        {"id": 13, "created_at": "2026-11-02T10:00:00Z", "text": "Apply here | ML Engineer | contract<p>x"},
        {"id": 14, "created_at": "2026-11-02T10:00:00Z", "text": "Beta Cloud | AI Engineer | REMOTE<p>Cloud & AI services company with a growing GenAI practice. hiring@beta.example"},
    ]}  # fmt: skip
    found = HnHiringTriggerConnector.parse(thread, S.topic_term_list)
    assert [f.organization_name for f in found] == [
        "Acme Robotics",
        "Beta Cloud",
    ]  # plain full-time hiring dropped
    acme, beta = found
    assert (
        acme.kind is SignalKind.COMPANY_SIGNAL
        and acme.channel_hint == "proactive_buyer"
        and acme.contact_route is None
    )
    assert not any("careers" in r.value for r in acme.routes)  # a careers page is not a buyer route
    assert any(r.value == "jane@acme.example" and r.role == "Head of AI" for r in acme.routes)
    assert (
        beta.kind is SignalKind.PARTNER_ACTIVITY
        and beta.channel_hint == "agency"
        and beta.partnership_class == "potential"
    )
    assert any("recruiting inbox" in (r.role or "") for r in beta.routes)
    opp = session.get(
        Opportunity, ingest_finding(session, acme, "hn_hiring_triggers", None, 90).opportunity_id
    )
    a = assess_opportunity(session, opp, S)  # type: ignore[arg-type]
    apply_assessment(opp, a)  # type: ignore[arg-type]
    assert opp.category == "speculative_prospect" and opp.channel == "proactive_buyer"  # type: ignore[union-attr]
    assert opp.triage == "needs_verification" and "trigger" in a.reasons[-1].lower()  # type: ignore[union-attr]
    fit = opportunity_fit(session, opp)  # type: ignore[arg-type]
    assert fit["buying_intent"].startswith("Business trigger only: NOT a request")
    assert fit["verdict"] == "research then decide" and "Unconfirmed inference" in fit["problem"]
    assert any("trigger, not a request" in c for c in fit["approach"]["cautions"])
    assert fit["decision_roles"] and fit["decision_roles"][0]["role_as_printed"] == "Head of AI"


def test_ted_planning_notices_become_triggers_not_open_calls() -> None:
    payload = json.loads((FIX / "ted_api_recorded_2026-11-03.json").read_text())
    found = TedPlanningNoticeConnector.parse(payload)
    assert found and all(
        f.kind is SignalKind.COMPANY_SIGNAL and f.channel_hint == "proactive_buyer" for f in found
    )
    assert all(
        f.source_status == "unknown" and "trigger, not an open call" in f.title for f in found
    )
    assert "notice-type = pin-only" in TedPlanningNoticeConnector(S).build_query(
        __import__("app.connectors.base", fromlist=["DiscoveryQuery"]).DiscoveryQuery()
    )


def test_planning_notice_title_marker_is_applied_exactly_once() -> None:
    import httpx

    payload = json.loads((FIX / "ted_api_recorded_2026-11-03.json").read_text())
    c = TedPlanningNoticeConnector(
        S, httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    )
    from app.connectors.base import DiscoveryQuery

    titles = [f.title for f in c.discover(DiscoveryQuery()).findings]
    assert titles and all(t.count("planning notice") == 1 for t in titles)
