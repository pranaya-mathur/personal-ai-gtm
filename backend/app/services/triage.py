"""Two-stage qualification, fully deterministic (no model call).

Stage A  technical relevance: does the SOURCE describe AI implementation work, not merely mention
         an AI word?  relevant | unclear | not_relevant
Stage B  commercial eligibility: external requirement, still open, relevant scope, delivery
         model, realistic size, contact route, budget.  Each criterion is pass | fail | unknown.

Outcome: actionable | needs_verification | rejected(+reason). Unknowns are listed as missing
information instead of being guessed. An AI advisory result may refine Stage A but can never
create an `actionable` on its own, and nothing here sets human verification or the evidence
category (confirmed demand is never auto-promoted).
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import Contact, Evidence, Opportunity, OpportunityEvidence, Organization
from app.models.enums import (
    OpportunityCategory,
    OpportunityStatus,
    OrganizationKind,
    SourceStatus,
    TechnicalRelevance,
    Triage,
)

# Short acronyms must be written in capitals (Italian/Finnish "ai", English "rag" are common words).
_CASE_SENSITIVE_MAX_LEN = 4
_IMPLEMENTATION = re.compile(
    r"\b(develop\w*|implement\w*|build\w*|deploy\w*|design\w*|integrat\w*|deliver\w*|"
    r"create\w*|pilot|prototype|train\w*|operat\w*|provision\w*|introduc\w*|establish\w*)\b",
    re.I,
)
_REMOTE = re.compile(r"\b(remote(ly)?|off-?site|work from home|distributed team)\b", re.I)
_ONSITE = re.compile(
    r"\b(on-?site|on site|physical presence|on the premises|at the premises)\b", re.I
)
_SUBCONTRACT = re.compile(r"\b(sub-?contract\w*|consorti\w+|joint venture|partnership of)\b", re.I)
_LARGE_CONDITIONS = re.compile(
    r"\b(minimum (annual )?turnover|turnover of at least|framework agreement|"
    r"dynamic purchasing system|security clearance)\b",
    re.I,
)
_AMOUNT = re.compile(r"(\d[\d.,]*)\s*([A-Za-z]{3})?")
_CURRENCIES = {"EUR", "USD", "GBP"}  # compared numerically; anything else stays unknown


def _term_regex(term: str) -> re.Pattern[str]:
    flags = 0 if (len(term) <= _CASE_SENSITIVE_MAX_LEN and " " not in term) else re.I
    word = term.upper() if flags == 0 else re.escape(term)
    return re.compile(rf"(?<!\w){word}(?!\w)", flags)


def find_terms(text: str, terms: list[str]) -> list[tuple[str, int]]:
    hits: list[tuple[str, int]] = []
    for t in terms:
        m = _term_regex(t).search(text)
        if m:
            hits.append((t, m.start()))
    return hits


@dataclass
class AiSignal:
    """Validated AI advisory input (already claim-checked upstream)."""

    ai_project: str | None = None  # explicit | implied | none
    verdict: str | None = None  # supported | partially_supported | unsupported


@dataclass
class Assessment:
    technical_relevance: TechnicalRelevance
    triage: Triage
    rejection_reason: str | None
    stage_a: dict[str, Any]
    criteria: dict[str, dict[str, str]]
    missing: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    next_action: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "stage_a": self.stage_a,
            "stage_b": self.criteria,
            "missing": self.missing,
            "reasons": self.reasons,
            "next_action": self.next_action,
            "assessed_at": datetime.now(UTC).isoformat(),
        }


def stage_a(title: str, text: str, terms: list[str]) -> tuple[TechnicalRelevance, dict[str, Any]]:
    in_title = find_terms(title, terms)
    body = text
    hits = find_terms(f"{title}\n{body}", terms)
    distinct = sorted({t for t, _ in hits})
    info: dict[str, Any] = {"terms": distinct, "in_title": [t for t, _ in in_title]}
    if not distinct:
        info["why"] = "no AI term in the source text"
        return TechnicalRelevance.NOT_RELEVANT, info
    if in_title:
        info["why"] = "AI term in the notice title"
        return TechnicalRelevance.RELEVANT, info
    # a term used with an implementation verb in a sentence-sized window, and not in a tech list
    full = f"{title}\n{body}"
    for term, pos in hits:
        window = full[max(0, pos - 80) : pos + len(term) + 80]
        if _IMPLEMENTATION.search(window) and window.count(",") < 3:
            info["why"] = f"{term!r} appears with an implementation verb"
            return TechnicalRelevance.RELEVANT, info
    if len(distinct) >= 2:
        info["why"] = "several distinct AI terms"
        return TechnicalRelevance.RELEVANT, info
    info["why"] = "AI is mentioned (possibly in a list) but no AI implementation is described"
    return TechnicalRelevance.UNCLEAR, info


def _budget(budget_text: str | None, ceiling: float) -> tuple[str, str]:
    if not budget_text:
        return "unknown", "budget not stated"
    m = _AMOUNT.search(budget_text.replace(" ", ""))
    cur = (m.group(2) or "").upper() if m else ""
    if not m or cur not in _CURRENCIES:
        return "unknown", f"budget {budget_text!r} (currency not comparable)"
    try:
        raw = m.group(1)
        value = (
            float(raw.replace(",", ""))
            if "," in raw and "." in raw
            else float(raw.replace(",", "."))
        )
    except ValueError:
        return "unknown", f"budget {budget_text!r} could not be parsed"
    if value > ceiling:
        return "fail", f"stated value {budget_text} exceeds {ceiling:,.0f} (large procurement)"
    return "pass", f"stated value {budget_text} within {ceiling:,.0f}"


def assess(
    *,
    title: str,
    text: str,
    category: str,
    org_kind: str | None,
    source_status: str,
    deadline: datetime | None,
    budget_text: str | None,
    contact_route: str | None,
    known_contacts: int,
    settings: Settings,
    ai: AiSignal | None = None,
    now: datetime | None = None,
) -> Assessment:
    now = now or datetime.now(UTC)
    relevance, a_info = stage_a(title, text, settings.topic_term_list)
    a_info["source"] = "deterministic"
    if ai is not None:
        if relevance is TechnicalRelevance.UNCLEAR and ai.ai_project == "explicit":
            relevance, a_info["source"] = TechnicalRelevance.RELEVANT, "ai_confirmed"
        elif relevance is TechnicalRelevance.RELEVANT and ai.verdict == "unsupported":
            relevance, a_info["source"] = TechnicalRelevance.UNCLEAR, "ai_downgraded"

    partnership_track = category != OpportunityCategory.CONFIRMED_DEMAND or (
        org_kind == OrganizationKind.AGENCY and category == OpportunityCategory.VERIFIED_PARTNERSHIP
    )
    crit: dict[str, dict[str, str]] = {}

    def put(name: str, result: str, detail: str) -> None:
        crit[name] = {"result": result, "detail": detail}

    # --- partnership track: agencies are never "actionable projects" -----------------------
    if partnership_track:
        if category == OpportunityCategory.VERIFIED_PARTNERSHIP:
            put(
                "external_requirement",
                "unknown",
                "partnership/subcontracting MODEL evidenced; no current project stated",
            )
        else:
            put(
                "external_requirement",
                "fail",
                "no stated need or delivery model; potential partner only",
            )
        put(
            "scope",
            "pass" if relevance is TechnicalRelevance.RELEVANT else "unknown",
            a_info["why"],
        )
        put(
            "contact_route",
            "pass" if (contact_route or known_contacts) else "unknown",
            contact_route or "no contact route in source",
        )
        missing = [f"{k}: {v['detail']}" for k, v in crit.items() if v["result"] == "unknown"]
        nxt = (
            "Review the source; decide whether to approach (no current project is stated)"
            if category == OpportunityCategory.VERIFIED_PARTNERSHIP
            else "Potential partner only: find a source that states an actual need before outreach"
        )
        if relevance is TechnicalRelevance.NOT_RELEVANT:
            relevance_out = (
                TechnicalRelevance.UNCLEAR
            )  # agencies are judged on the model, not wording
        else:
            relevance_out = relevance
        return Assessment(
            relevance_out,
            Triage.NEEDS_VERIFICATION,
            None,
            a_info,
            crit,
            missing,
            ["agency record: classified by what the source evidences, never inferred"],
            nxt,
        )

    # --- project track ----------------------------------------------------------------------
    put("external_requirement", "pass", "source is a call / request for external delivery")
    status = source_status
    if status == SourceStatus.UNKNOWN and deadline is not None:
        status = SourceStatus.OPEN if deadline > now else SourceStatus.CLOSED
    if status == SourceStatus.OPEN:
        put("active", "pass", f"open; deadline {deadline:%Y-%m-%d}" if deadline else "open")
    elif status in (SourceStatus.CLOSED, SourceStatus.AWARDED):
        put("active", "fail", f"source is {status}")
    else:
        put("active", "unknown", "no deadline or open/closed status in the source")
    put(
        "scope",
        {"relevant": "pass", "unclear": "unknown", "not_relevant": "fail"}[str(relevance)],
        a_info["why"],
    )
    if _SUBCONTRACT.search(text):
        put("remote_or_subcontract", "pass", "subcontracting/consortium arrangements mentioned")
    elif _REMOTE.search(text):
        put("remote_or_subcontract", "pass", "remote delivery mentioned")
    elif _ONSITE.search(text):
        put(
            "remote_or_subcontract",
            "unknown",
            "on-site delivery mentioned; remote delivery not confirmed",
        )
    else:
        put("remote_or_subcontract", "unknown", "delivery location/model not stated")
    b_res, b_detail = _budget(budget_text, settings.small_team_max_value)
    if b_res == "unknown" and _LARGE_CONDITIONS.search(text):
        b_res, b_detail = (
            "unknown",
            "framework/turnover/clearance conditions suggest a large procurement",
        )
    put("realistic_for_small_team", b_res, b_detail)
    put(
        "contact_route",
        "pass" if (contact_route or known_contacts) else "unknown",
        contact_route
        or (
            "known contact"
            if known_contacts
            else "only the notice page; no buyer contact in source"
        ),
    )
    put("budget", "pass", budget_text if budget_text else "UNKNOWN (not stated in source)")

    missing = [f"{k}: {v['detail']}" for k, v in crit.items() if v["result"] == "unknown"]
    if not budget_text:
        missing.append("budget: not stated")
    reasons: list[str] = []
    codes: list[str] = []
    if status in (SourceStatus.CLOSED, SourceStatus.AWARDED):
        codes.append(str(status))
        reasons.append(f"opportunity is {status}")
    if relevance is TechnicalRelevance.NOT_RELEVANT:
        codes.append("not_relevant")
        reasons.append("source text describes no AI work")
    reason = ",".join(codes) or None
    if reason is not None:
        return Assessment(
            relevance,
            Triage.REJECTED,
            reason,
            a_info,
            crit,
            [],
            reasons,
            "No action: rejected (" + reason + ")",
        )

    hard_fail = [k for k, v in crit.items() if v["result"] == "fail"]
    core_ok = all(
        crit[k]["result"] == "pass"
        for k in ("external_requirement", "active", "scope", "contact_route")
    )
    if core_ok and not hard_fail:
        reasons.append("relevant, open, external requirement with a buyer contact route")
        nxt = (
            "Open the notice, check eligibility and size, then decide whether to apply"
            if missing
            else "Review and decide whether to apply"
        )
        return Assessment(relevance, Triage.ACTIONABLE, None, a_info, crit, missing, reasons, nxt)
    if hard_fail:
        reasons.append("failed: " + ", ".join(hard_fail))
    reasons.append(
        "unconfirmed: " + ", ".join(k for k, v in crit.items() if v["result"] == "unknown")
    )
    nxt = "Verify manually: " + "; ".join(missing[:3]) if missing else "Verify the source manually"
    return Assessment(
        relevance, Triage.NEEDS_VERIFICATION, None, a_info, crit, missing, reasons, nxt
    )


def assess_opportunity(
    session: Session, opp: Opportunity, settings: Settings, ai: AiSignal | None = None
) -> Assessment:
    texts = list(
        session.execute(
            select(Evidence.original_text)
            .join(OpportunityEvidence, OpportunityEvidence.evidence_id == Evidence.id)
            .where(OpportunityEvidence.opportunity_id == opp.id)
        ).scalars()
    )
    org = session.get(Organization, opp.organization_id) if opp.organization_id else None
    contacts = (
        len(session.execute(select(Contact.id).where(Contact.organization_id == org.id)).all())
        if org
        else 0
    )
    return assess(
        title=opp.title,
        text="\n".join(texts),
        category=opp.category,
        org_kind=org.kind if org else None,
        source_status=opp.source_status,
        deadline=opp.deadline,
        budget_text=opp.budget_text,
        contact_route=opp.contact_route,
        known_contacts=contacts,
        settings=settings,
        ai=ai,
    )


def apply_assessment(opp: Opportunity, a: Assessment) -> dict[str, Any]:
    """Writes the assessment and maps it to the workflow status. Returns the previous state for
    the audit trail. Never touches category or human verification."""
    previous = {
        "status": opp.status,
        "triage": opp.triage,
        "technical_relevance": opp.technical_relevance,
        "category": opp.category,
    }
    opp.technical_relevance = a.technical_relevance
    opp.triage = a.triage
    opp.rejection_reason = a.rejection_reason
    opp.assessment = a.to_json()
    if a.triage is Triage.REJECTED:
        if opp.status in (OpportunityStatus.NEW, OpportunityStatus.QUALIFIED):
            opp.status = OpportunityStatus.DISQUALIFIED
    elif a.triage is Triage.ACTIONABLE:
        if opp.status == OpportunityStatus.NEW:
            opp.status = OpportunityStatus.QUALIFIED
    elif opp.status == OpportunityStatus.QUALIFIED:
        opp.status = OpportunityStatus.NEW  # earlier keyword-only qualification is withdrawn
    return previous
