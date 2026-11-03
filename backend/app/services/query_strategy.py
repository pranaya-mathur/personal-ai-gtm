"""Configurable intent-query strategy: variations, dedupe, effectiveness, no auto paid spend.

AI may propose hypotheses elsewhere; this module only expands configured templates and records
what actually produced validated commercial classes. Paid providers are never selected here.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.entities import DiscoveryQueryStat
from app.services.commercial_class import (
    ACTIVE_AGENCY_PARTNER_NEED,
    CONFIRMED_ACTIVE_REQUEST,
    NEEDS_VERIFICATION,
)

# Geography / industry / service facets used to expand templates without inventing evidence.
GEO_FACETS = ("US", "UK", "EU", "UAE", "Canada", "Australia", "Singapore", "India")
SERVICE_FACETS = (
    "AI agent",
    "RAG",
    "document processing",
    "chatbot",
    "workflow automation",
    "voice AI",
    "computer vision",
    "LLM integration",
)
INDUSTRY_FACETS = (
    "healthcare",
    "fintech",
    "logistics",
    "legal",
    "education",
    "e-commerce",
    "manufacturing",
)

_SPACE = re.compile(r"\s+")

# Outcomes that count as commercially useful for query ranking (not mere keyword hits).
_USEFUL = frozenset({CONFIRMED_ACTIVE_REQUEST, ACTIVE_AGENCY_PARTNER_NEED, NEEDS_VERIFICATION})


@dataclass(frozen=True)
class PlannedQuery:
    channel: str  # agency | buyer
    text: str
    origin: str  # configured | expanded | rotated
    fingerprint: str


def fingerprint(text: str) -> str:
    norm = _SPACE.sub(" ", text.strip().lower())
    return hashlib.sha256(norm.encode()).hexdigest()[:24]


def _expand(template: str, facets: Iterable[str], limit: int) -> list[str]:
    """Replace {facet} once per template; skip templates without a placeholder."""
    if "{facet}" not in template:
        return [template]
    out: list[str] = []
    for f in facets:
        out.append(template.replace("{facet}", f))
        if len(out) >= limit:
            break
    return out


def build_plan(settings: Settings, *, max_per_channel: int | None = None) -> list[PlannedQuery]:
    """Build a deduplicated query plan from settings templates + facet expansions."""
    cap = max_per_channel or settings.query_plan_max_per_channel
    agency_tpl = settings.intent_queries_agency_list
    buyer_tpl = settings.intent_queries_buyer_list
    plan: list[PlannedQuery] = []
    seen: set[str] = set()

    def add(channel: str, text: str, origin: str) -> None:
        fp = fingerprint(text)
        if fp in seen or not text.strip():
            return
        seen.add(fp)
        plan.append(PlannedQuery(channel=channel, text=text.strip(), origin=origin, fingerprint=fp))

    for channel, templates in (("agency", agency_tpl), ("buyer", buyer_tpl)):
        # 1) configured templates as-is
        for t in templates:
            add(channel, t, "configured")
        # 2) facet expansions from templates that include {facet}
        expanded: list[str] = []
        for t in templates:
            expanded += _expand(t, SERVICE_FACETS, 4)
            expanded += _expand(t, GEO_FACETS, 3)
            expanded += _expand(t, INDUSTRY_FACETS, 2)
        for t in expanded:
            if len([p for p in plan if p.channel == channel]) >= cap:
                break
            add(channel, t, "expanded")
        # trim channel to cap (prefer configured first — already ordered)
        channel_items = [p for p in plan if p.channel == channel][:cap]
        plan = [p for p in plan if p.channel != channel] + channel_items

    return plan


def prefer_effective(
    session: Session, plan: list[PlannedQuery], *, min_searches: int = 2
) -> list[PlannedQuery]:
    """Deprioritize queries that repeatedly yield zero useful commercial classes."""
    rows = {r.fingerprint: r for r in session.execute(select(DiscoveryQueryStat)).scalars()}
    good: list[PlannedQuery] = []
    cold: list[PlannedQuery] = []
    weak: list[PlannedQuery] = []
    for p in plan:
        row = rows.get(p.fingerprint)
        if row is None or row.searches < min_searches:
            cold.append(p)
            continue
        useful = row.confirmed_active + row.agency_partner_need + row.needs_verification
        if useful == 0 and row.urls_retrieved >= 5:
            weak.append(p)  # keep at end; do not delete — still inspectable
        else:
            good.append(p)
    return good + cold + weak


def record_search(
    session: Session,
    *,
    provider: str,
    planned: PlannedQuery,
    urls: int = 0,
    pages_ok: int = 0,
    pages_blocked: int = 0,
    cost_usd: float = 0.0,
) -> DiscoveryQueryStat:
    row = session.execute(
        select(DiscoveryQueryStat).where(
            DiscoveryQueryStat.fingerprint == planned.fingerprint,
            DiscoveryQueryStat.provider == provider,
        )
    ).scalar_one_or_none()
    if row is None:
        row = DiscoveryQueryStat(
            fingerprint=planned.fingerprint,
            provider=provider,
            channel=planned.channel,
            query_text=planned.text[:512],
        )
        session.add(row)
    row.searches += 1
    row.urls_retrieved += urls
    row.pages_ok += pages_ok
    row.pages_blocked += pages_blocked
    row.cost_usd = float(row.cost_usd) + cost_usd
    session.flush()
    return row


def record_outcome(
    session: Session,
    *,
    provider: str,
    query_fingerprint: str | None,
    commercial_class: str,
    duplicate: bool = False,
) -> None:
    if not query_fingerprint:
        return
    row = session.execute(
        select(DiscoveryQueryStat).where(
            DiscoveryQueryStat.fingerprint == query_fingerprint,
            DiscoveryQueryStat.provider == provider,
        )
    ).scalar_one_or_none()
    if row is None:
        return
    if duplicate:
        row.duplicates += 1
    elif commercial_class == CONFIRMED_ACTIVE_REQUEST:
        row.confirmed_active += 1
    elif commercial_class == ACTIVE_AGENCY_PARTNER_NEED:
        row.agency_partner_need += 1
    elif commercial_class == NEEDS_VERIFICATION:
        row.needs_verification += 1
    else:
        row.false_positives += 1
    session.flush()


def effectiveness_report(session: Session) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for r in session.execute(
        select(DiscoveryQueryStat).order_by(DiscoveryQueryStat.searches.desc())
    ).scalars():
        useful = r.confirmed_active + r.agency_partner_need + r.needs_verification
        out.append(
            {
                "fingerprint": r.fingerprint,
                "provider": r.provider,
                "channel": r.channel,
                "query": r.query_text,
                "searches": r.searches,
                "urls_retrieved": r.urls_retrieved,
                "pages_ok": r.pages_ok,
                "pages_blocked": r.pages_blocked,
                "useful": useful,
                "false_positives": r.false_positives,
                "duplicates": r.duplicates,
                "cost_usd": float(r.cost_usd),
                "cost_per_useful": (float(r.cost_usd) / useful) if useful else None,
            }
        )
    return out
