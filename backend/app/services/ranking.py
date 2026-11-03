"""Transparent commercial ranking with separate buyer and agency rubrics.

Orders records for human attention. Never implies win probability. Unknown budget stays neutral
(neither boosted nor punished beyond missing contactability signals).
"""

from __future__ import annotations

from typing import Any

from app.models.enums import OpportunityCategory, Triage
from app.services.commercial_class import commercial_class as class_of


def score_buyer(
    *,
    category: str,
    triage: str,
    confidence: float,
    research: dict[str, object],
    deal_size_result: str | None = None,
    age_days: int | None = None,
) -> tuple[float, dict[str, float]]:
    """Direct-buyer ranking: active confirmed demand outranks speculative keyword density."""
    intent = {
        Triage.ACTIONABLE: 40.0,
        Triage.NEEDS_VERIFICATION: 18.0,
        Triage.REJECTED: 0.0,
        Triage.UNASSESSED: 5.0,
    }.get(triage, 5.0)
    if category == OpportunityCategory.SPECULATIVE_PROSPECT:
        intent = min(intent, 8.0)
    elif category == OpportunityCategory.CONFIRMED_DEMAND and triage == Triage.ACTIONABLE:
        intent = 40.0

    criteria: dict[str, float] = {
        "buying_intent": intent,
        "source_confidence": confidence * 15,
        "freshness": _freshness(research, age_days),
        "contactability": 12.0 if research.get("known_contacts") or research.get("has_contact_route") else 0.0,
        "source_confirmed": 8.0 if research.get("source_confirmed") else 0.0,
        "dated_and_sourced": 10.0
        if research.get("has_publication_date") and research.get("has_source_url")
        else 0.0,
        "deal_fit": _deal_fit(deal_size_result),
    }
    return round(sum(criteria.values()), 1), criteria


def score_agency(
    *,
    category: str,
    triage: str,
    confidence: float,
    research: dict[str, object],
    age_days: int | None = None,
) -> tuple[float, dict[str, float]]:
    """Agency ranking: active partner-seeking > verified model > potential-only."""
    if category == OpportunityCategory.CONFIRMED_DEMAND and triage != Triage.REJECTED:
        model = 38.0  # stated need (still may need human currency check)
    elif category == OpportunityCategory.VERIFIED_PARTNERSHIP:
        model = 28.0
    elif category == OpportunityCategory.SPECULATIVE_PROSPECT:
        model = 10.0
    else:
        model = 5.0
    if triage == Triage.REJECTED:
        model = 0.0

    criteria: dict[str, float] = {
        "partner_intent": model,
        "source_confidence": confidence * 15,
        "freshness": _freshness(research, age_days),
        "contactability": 15.0 if research.get("known_contacts") or research.get("has_contact_route") else 0.0,
        "source_confirmed": 8.0 if research.get("source_confirmed") else 0.0,
        "dated_and_sourced": 10.0
        if research.get("has_publication_date") and research.get("has_source_url")
        else 0.0,
        "relationship_potential": 8.0
        if category in (OpportunityCategory.VERIFIED_PARTNERSHIP, OpportunityCategory.CONFIRMED_DEMAND)
        else 0.0,
    }
    return round(sum(criteria.values()), 1), criteria


def score_opportunity(
    *,
    channel: str,
    category: str,
    triage: str,
    confidence: float,
    research: dict[str, object],
    deal_size_result: str | None = None,
    age_days: int | None = None,
) -> tuple[float, dict[str, Any]]:
    if channel == "agency":
        score, crit = score_agency(
            category=category,
            triage=triage,
            confidence=confidence,
            research=research,
            age_days=age_days,
        )
        rubric = "agency_partnership"
    else:
        score, crit = score_buyer(
            category=category,
            triage=triage,
            confidence=confidence,
            research=research,
            deal_size_result=deal_size_result,
            age_days=age_days,
        )
        rubric = "direct_buyer"
    meta: dict[str, Any] = dict(crit)
    meta["rubric"] = rubric
    meta["commercial_class"] = class_of(category=category, triage=triage, channel=channel)
    return score, meta


def _freshness(research: dict[str, object], age_days: int | None) -> float:
    if research.get("all_fresh") is False:
        return 0.0
    if age_days is None:
        return 6.0 if research.get("all_fresh") else 0.0
    if age_days <= 14:
        return 15.0
    if age_days <= 45:
        return 10.0
    if age_days <= 90:
        return 5.0
    return 0.0


def _deal_fit(result: str | None) -> float:
    if result == "pass":
        return 10.0
    if result == "fail":
        return 0.0
    return 4.0  # unknown budget: neutral, not a boost
