"""Actor specs: the ONLY place that knows an Actor's input/output field names.

Every field below comes from the Actor's published input/output schema as read via the Apify
Store on 2026-11-03 (read-only; no Actor was run). Re-validate before enabling after an Actor
update. A spec with reviewed=False cannot be enabled.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.connectors.base import DiscoveryQuery, RawContact, RawFinding, SignalKind

TED_COUNTRIES = {
    "DEU", "FRA", "ITA", "ESP", "POL", "ROU", "NLD", "BEL", "CZE", "GRC", "PRT", "SWE", "HUN",
    "AUT", "BGR", "DNK", "FIN", "SVK", "IRL", "HRV", "LTU", "SVN", "LVA", "EST", "CYP", "LUX",
    "MLT", "NOR", "ISL", "LIE", "CHE",
}  # fmt: skip


@dataclass(frozen=True)
class ActorSpec:
    connector_name: str
    actor_id: str
    kind: SignalKind
    validated_on: str
    start_usd: float  # per published pricing, FREE tier
    per_result_usd: float
    max_results_cap: int
    reviewed: bool
    build_input: Callable[[DiscoveryQuery, int], dict[str, Any]]
    map_item: Callable[[dict[str, Any], DiscoveryQuery], RawFinding | None]
    notes: str = ""

    def estimate_usd(self, max_results: int) -> float:
        """Upper bound: start fee + every permitted result billed."""
        return round(self.start_usd + self.per_result_usd * max_results, 6)


def parse_date(value: Any) -> datetime | None:
    """Tolerant parser; returns None (=> treated as undated/stale) rather than guessing."""
    if not isinstance(value, str) or not value.strip():
        return None
    v = value.strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(v[:10] if fmt != "%Y%m%d" else v[:8], fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        return None


def _s(item: dict[str, Any], key: str) -> str | None:
    v = item.get(key)
    return v.strip() if isinstance(v, str) and v.strip() else None


# ---------------- foxlabs/ted-tenders (EU TED official API) ----------------
def _ted_input(q: DiscoveryQuery, max_results: int) -> dict[str, Any]:
    days = q.max_age_days
    preset = "last_7_days" if days <= 7 else "last_30_days" if days <= 30 else "last_90_days"
    run_input: dict[str, Any] = {
        "datePreset": preset,
        "noticeTypes": ["cn", "pin-cfc-standard"],  # open calls for competition only
        "cpvCodes": ["72000000", "48000000"],  # IT services & software packages
        "language": "ENG",
        "maxResults": max_results,
    }
    if q.keywords:
        run_input["keywords"] = ", ".join(q.keywords)
    countries = [r.upper() for r in q.regions if r.upper() in TED_COUNTRIES]
    if countries:
        run_input["countries"] = countries
    return run_input


def _ted_map(item: dict[str, Any], q: DiscoveryQuery) -> RawFinding | None:
    title, url = _s(item, "title"), _s(item, "detailUrl")
    if not title or not url:
        return None  # no original source => not usable evidence
    deadline = parse_date(item.get("deadlineDate"))
    if deadline is not None and deadline < datetime.now(UTC):
        return None  # tender already closed
    description = _s(item, "description")
    return RawFinding(
        kind=SignalKind.PROJECT_REQUEST,
        title=title,
        source_url=url,
        original_text=f"{title}\n\n{description}" if description else title,
        published_at=parse_date(item.get("publicationDate")),
        organization_name=_s(item, "buyerName"),
        organization_country=_s(item, "buyerCountry"),
        provider_confidence=0.7,  # official notice: stated need, but not yet source-checked
    )


# ---------------- foxlabs/worldbank-tenders (World Bank official API) ----------------
def _wb_input(q: DiscoveryQuery, max_results: int) -> dict[str, Any]:
    days = q.max_age_days
    preset = "last_7_days" if days <= 7 else "last_30_days" if days <= 30 else "last_90_days"
    run_input: dict[str, Any] = {
        "status": "open",
        "datePreset": preset,
        "maxResults": max_results,
        "includeExpired": False,
    }
    if q.keywords:
        run_input["searchTerm"] = q.keywords[0]  # single full-text term per the schema
    if q.regions:
        run_input["countries"] = q.regions
    return run_input


def _wb_map(item: dict[str, Any], q: DiscoveryQuery) -> RawFinding | None:
    name, url = _s(item, "projectName"), _s(item, "detailUrl")
    if not name or not url:
        return None
    desc = _s(item, "bidDescription")
    contacts: list[RawContact] = []
    cname, cemail = _s(item, "contactName"), _s(item, "contactEmail")
    if cname and cemail and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", cemail):
        contacts.append(RawContact(full_name=cname, email=cemail))  # as printed in the notice
    return RawFinding(
        kind=SignalKind.PROJECT_REQUEST,
        title=name,
        source_url=url,
        original_text=f"{name}\n\n{desc}" if desc else name,
        published_at=parse_date(item.get("noticeDate")),
        organization_name=_s(item, "contactOrganization"),
        organization_country=_s(item, "country"),
        contacts=contacts,
        provider_confidence=0.7,
    )


# ---------------- danthedataman/clutch-agency-directory (NOT enabled) ----------------
def _clutch_input(q: DiscoveryQuery, max_results: int) -> dict[str, Any]:
    return {
        "category": "agencies/artificial-intelligence",
        "location": q.regions[0] if q.regions else "",
        "maxItems": max_results,
    }


def _clutch_map(item: dict[str, Any], q: DiscoveryQuery) -> RawFinding | None:
    name, url = _s(item, "name"), _s(item, "profileUrl")
    summary = _s(item, "summary")
    if not name or not url or not summary:
        return None
    # A directory listing proves the agency exists, NOT partner activity: company signal only.
    return RawFinding(
        kind=SignalKind.COMPANY_SIGNAL,
        title=f"{name} (agency directory listing)",
        source_url=url,
        original_text=summary,
        published_at=parse_date(item.get("scrapedAt")),
        organization_name=name,
        organization_country=_s(item, "addressCountry"),
        provider_confidence=0.3,
    )


SPECS: dict[str, ActorSpec] = {
    s.connector_name: s
    for s in (
        ActorSpec(
            "apify_ted_tenders", "foxlabs/ted-tenders", SignalKind.PROJECT_REQUEST,
            "2026-11-03", 0.00005, 0.004, 100, True, _ted_input, _ted_map,
            notes="Official EU TED API; open IT/software calls for competition.",
        ),
        ActorSpec(
            "apify_worldbank_tenders", "foxlabs/worldbank-tenders", SignalKind.PROJECT_REQUEST,
            "2026-11-03", 0.00005, 0.0025, 100, True, _wb_input, _wb_map,
            notes="Official World Bank procurement API; open notices with printed contacts.",
        ),
        ActorSpec(
            "apify_clutch_agencies", "danthedataman/clutch-agency-directory",
            SignalKind.COMPANY_SIGNAL, "2026-11-03", 0.00005, 0.00085, 50, False,
            _clutch_input, _clutch_map,
            notes="BLOCKED: needs residential proxies to bypass blocking and Clutch terms "
            "must be reviewed; do not enable until a permitted source is confirmed.",
        ),
    )
}  # fmt: skip
