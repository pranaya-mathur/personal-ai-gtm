"""Opportunity fit, personalised approach, pipeline stage. Deterministic and evidence-grounded.

Every statement either quotes the stored evidence or says it is an unconfirmed inference. No
experience, reference, budget, contact or decision-maker is invented: unknowns are listed.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ContactRoute,
    Deal,
    Evidence,
    Interaction,
    OnboardingItem,
    Opportunity,
    OpportunityEvidence,
    Organization,
    OutreachDraft,
)
from app.models.enums import DealStage, OutreachStatus, Triage
from app.services.capabilities import get_profile, match_capabilities
from app.services.commercial_class import (
    ACTIVE_AGENCY_PARTNER_NEED,
    CONFIRMED_ACTIVE_REQUEST,
    LABELS,
    POTENTIAL_NO_DEMAND,
    VERIFIED_AGENCY_MODEL,
    commercial_class,
)
from app.services.project_type import project_type

PIPELINE = [
    "discovered", "researched", "qualified", "outreach_prepared", "approved", "contacted",
    "responded", "meeting", "proposal", "negotiation", "won", "onboarding", "lost",
]  # fmt: skip

SCOPE_TEMPLATES: dict[str, list[str]] = {
    "rag_knowledge": ["Discovery: sources, access and quality bar", "Ingestion, chunking and retrieval pipeline", "Answer generation with citations and an evaluation set", "Integration, access control and handover"],
    "ai_agents": ["Discovery: tasks, tools and success criteria", "Agent design: tools, guardrails, human-in-the-loop", "Build and evaluate on real cases", "Deployment, monitoring and handover"],
    "chatbot_support": ["Discovery: top intents, knowledge sources, channels", "Assistant build with grounding and fallbacks", "Evaluation on real conversations and safety review", "Channel integration, launch and handover"],
    "document_intelligence": ["Discovery: document types, volumes, accuracy targets", "Extraction/classification pipeline with confidence thresholds", "Review UI or queue for low-confidence items", "Integration with downstream systems and handover"],
    "workflow_automation": ["Discovery: process map and automation candidates", "Automation build with human approval points", "Pilot on a live workflow, measure time saved", "Rollout and documentation"],
    "llm_integration": ["Discovery: use case, data and constraints", "Prototype integration and prompt/eval design", "Hardening: cost, latency, safety, logging", "Deployment and handover"],
    "computer_vision": ["Discovery: images, labels and accuracy targets", "Model selection/training and evaluation", "Pipeline and integration", "Deployment and monitoring"],
    "ml_predictive": ["Discovery: target, data and baseline", "Model development and validation", "Integration and monitoring", "Handover"],
    "general_ai": ["Discovery workshop to turn the need into a scoped use case", "Prototype on real data", "Evaluation and decision on production build", "Production build and handover (separate scope)"],
    "other": ["Discovery workshop to confirm whether AI is the right solution", "Scoped proposal after discovery"],
}  # fmt: skip

_ROLE = re.compile(
    r"(head of|director|manager|lead|chief|cto|ceo|founder|procurement|partnerships?)", re.I
)


def evidence_rows(session: Session, opp: Opportunity) -> list[Evidence]:
    return list(
        session.execute(
            select(Evidence)
            .join(OpportunityEvidence, OpportunityEvidence.evidence_id == Evidence.id)
            .where(OpportunityEvidence.opportunity_id == opp.id)
            .order_by(Evidence.retrieved_at)
        ).scalars()
    )


def pipeline_stage(session: Session, opp: Opportunity) -> str:
    """Furthest point reached, derived from facts already stored (nothing is advanced silently)."""
    if opp.triage == Triage.REJECTED:
        return "rejected"
    deal = session.execute(select(Deal).where(Deal.opportunity_id == opp.id)).scalar_one_or_none()
    if deal is not None:
        if deal.stage == DealStage.LOST:
            return "lost"
        if deal.stage == DealStage.WON:
            has_items = session.execute(
                select(OnboardingItem.id).where(OnboardingItem.deal_id == deal.id).limit(1)
            ).first()
            return "onboarding" if has_items else "won"
        if deal.stage == DealStage.NEGOTIATION:
            return "negotiation"
        if deal.stage == DealStage.PROPOSAL:
            return "proposal"
        kinds = {
            k
            for (k,) in session.execute(
                select(Interaction.kind).where(Interaction.deal_id == deal.id)
            ).all()
        }
        if "meeting" in kinds or deal.stage == DealStage.CONVERSATION:
            return "meeting"
        if "email_received" in kinds:
            return "responded"
        if "email_sent" in kinds or deal.stage == DealStage.CONTACTED:
            return "contacted"
    drafts = {
        s
        for (s,) in session.execute(
            select(OutreachDraft.status).where(OutreachDraft.opportunity_id == opp.id)
        ).all()
    }
    if OutreachStatus.SENT in drafts:
        return "contacted"
    if OutreachStatus.APPROVED in drafts:
        return "approved"
    if OutreachStatus.PENDING_APPROVAL in drafts:
        return "outreach_prepared"
    if opp.triage == Triage.ACTIONABLE or opp.status == "qualified":
        return "qualified"
    if opp.triage == Triage.NEEDS_VERIFICATION:
        return "researched"
    return "discovered"


def _first_sentences(text: str, n: int = 2, limit: int = 380) -> str:
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+|\n+", text) if p.strip()]
    return " ".join(parts[:n])[:limit]


def opportunity_fit(session: Session, opp: Opportunity) -> dict[str, Any]:
    org = session.get(Organization, opp.organization_id) if opp.organization_id else None
    evs = evidence_rows(session, opp)
    text = f"{opp.title}\n" + "\n".join(e.original_text for e in evs)
    cls = commercial_class(category=opp.category, triage=opp.triage, channel=opp.channel)
    ptype = project_type(text)
    areas = get_profile(session)
    caps = match_capabilities(text, areas)
    routes = list(
        session.execute(select(ContactRoute).where(ContactRoute.opportunity_id == opp.id)).scalars()
    )
    assess = opp.assessment or {}
    missing = list(assess.get("missing", []))
    proactive = opp.channel == "proactive_buyer"
    request_like = cls in (CONFIRMED_ACTIVE_REQUEST, ACTIVE_AGENCY_PARTNER_NEED)

    if request_like:
        intent = "Stated request for external delivery (a person must still confirm it is current)"
    elif proactive:
        intent = "Business trigger only: NOT a request. No evidence this organisation wants outside help."
    elif cls == VERIFIED_AGENCY_MODEL:
        intent = "Evidence of a subcontracting/partner model; no current project stated"
    elif cls == POTENTIAL_NO_DEMAND:
        intent = "None evidenced"
    else:
        intent = "Unconfirmed: AI-related, something essential is still unverified"

    quote = _first_sentences(evs[0].original_text) if evs else ""
    if request_like:
        problem = f"Stated by the source: “{quote}”"
    elif proactive:
        problem = (
            "Unconfirmed inference (not stated by the source): the organisation appears to be "
            "building AI capability and may need delivery capacity or acceleration."
        )
    else:
        problem = f"Source text: “{quote}”" if quote else "Not stated."

    if not caps:
        match_note = "No stated capability area matches the evidence text."
    else:
        match_note = "; ".join(f"{c['label']} ({c['evidence_status']})" for c in caps)

    decision_roles = [
        {"route": r.value, "role_as_printed": r.role}
        for r in routes
        if r.role and _ROLE.search(r.role)
    ]

    # --- verdict -----------------------------------------------------------------------------
    reasons: list[str] = []
    if opp.triage == Triage.REJECTED:
        verdict = "skip"
        reasons.append(f"rejected: {opp.rejection_reason}")
    elif request_like and opp.triage == Triage.ACTIONABLE and routes:
        verdict = "pursue"
        reasons.append("open, relevant, with a published contact/application route")
    elif request_like:
        verdict = "verify first"
        reasons.append("stated request, but status/eligibility/route is not fully confirmed")
    elif proactive:
        verdict = "research then decide"
        reasons.append("a trigger, not demand: spend time only if the fit looks strong")
    else:
        verdict = "park"
        reasons.append("no current demand evidenced")
    if not caps:
        reasons.append("no matching capability area")
    deal_size = (assess.get("stage_b") or {}).get("deal_size", {})
    if deal_size.get("result") == "fail":
        reasons.append(deal_size.get("detail", "deal size outside the acceptable range"))
        if verdict == "pursue":
            verdict = "verify first"
    if opp.budget_text:
        reasons.append(f"stated budget: {opp.budget_text}")
    else:
        reasons.append("budget unknown")

    scope = SCOPE_TEMPLATES.get(ptype, SCOPE_TEMPLATES["other"])
    who = org.name if org and org.name else "the organisation"
    approach = {
        "angle": (
            f"Respond to {who}'s stated request" if request_like else f"Open a conversation with {who}"
        ),
        "proposed_engagement": (
            "Paid discovery/prototype first (scope below), then production build as a separate scope"
            if not request_like
            else "Short proposal against the stated requirement; start with the discovery phase"
        ),
        "next_action": (
            "Verify the source yourself, confirm eligibility/size, then approve an outreach draft"
            if request_like
            else "Research the trigger on the organisation's own site, find a technical decision-maker, then approve a draft"
        ),
        "cautions": [
            "Do not claim project experience: none is on file unless added to the capability profile",
            *(["This is a trigger, not a request: do not describe it as active demand"] if proactive else []),
            *(["Contact is a recruiting/procurement address: find the right technical owner first"] if any("hiring" in (r.role or "").lower() or "jobs" in r.value.lower() for r in routes) else []),
        ],
    }  # fmt: skip

    return {
        "opportunity_id": str(opp.id),
        "title": opp.title,
        "organization": org.name if org else None,
        "country": org.country if org else None,
        "domain": org.domain if org else None,
        "commercial_class": cls,
        "commercial_class_label": LABELS[cls],
        "channel": opp.channel,
        "why_relevant": f"{ptype.replace('_', ' ')} signal; {match_note}",
        "problem": problem,
        "buying_intent": intent,
        "evidence": [
            {
                "url": e.source_url,
                "published": e.published_at.isoformat() if e.published_at else None,
                "quote": _first_sentences(e.original_text, 3, 600),
                "provider": e.provider,
            }
            for e in evs[:3]
        ],
        "matched_capabilities": caps,
        "what_i_could_deliver": scope,
        "decision_roles": decision_roles,
        "contact_routes": [{"kind": r.kind, "value": r.value, "role": r.role} for r in routes],
        "verdict": verdict,
        "verdict_reasons": reasons,
        "approach": approach,
        "missing": missing,
        "confidence": (
            "high" if opp.triage == Triage.ACTIONABLE and routes else "medium" if request_like else "low"
        ),
        "pipeline_stage": pipeline_stage(session, opp),
    }  # fmt: skip


def outreach_from_fit(fit: dict[str, Any], contact_name: str | None) -> tuple[str, str]:
    """Subject and body. Experience and specifics the owner must supply stay as placeholders."""
    ev = fit["evidence"][0] if fit["evidence"] else None
    greeting = (
        f"Hi {contact_name}," if contact_name else "Hi [RECIPIENT NAME - not found in evidence],"
    )
    org = fit["organization"] or "your organisation"
    if fit["commercial_class"] in (CONFIRMED_ACTIVE_REQUEST, ACTIVE_AGENCY_PARTNER_NEED):
        opener = f"I read your request on {ev['url'] if ev else 'your site'}: “{ev['quote'][:220] if ev else ''}”."
    else:
        opener = (
            f"I came across {org} ({ev['url'] if ev else 'public information'}) and noticed: "
            f"“{ev['quote'][:200] if ev else ''}”."
        )
    caps = fit["matched_capabilities"]
    cap_line = f"I work on {caps[0]['label']}." if caps else "[ONE LINE ON RELEVANT CAPABILITY]"
    body = (
        f"{greeting}\n\n{opener}\n\n{cap_line}\n\n"
        f"[YOUR RELEVANT EXPERIENCE - add an approved, verifiable reference]\n\n"
        f"A possible first step: {fit['approach']['proposed_engagement'].lower()}.\n\n"
        "[PROPOSED NEXT STEP]\n"
    )
    topic = re.split(r"\s[–-]\s", fit.get("title") or "")[-1].strip()[:90] or "your AI project"
    return f"Re: {topic}", body
