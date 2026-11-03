"""Free, keyless, OFFICIAL public APIs for direct-buyer demand. No Actor, no scraping.

* TedApiConnector          EU TED v3 search API (api.ted.europa.eu), open notices
* WorldBankApiConnector    World Bank procurement notices API
* ContractsFinderConnector UK Contracts Finder OCDS API
* HackerNewsFreelancerConnector  "SEEKING FREELANCER" comments in the monthly Ask HN thread

Field names were checked against live responses on 2026-11-03 (see tests/fixtures/*recorded*).
Closed/awarded/expired notices are returned flagged, never dropped silently, so the triage can
reject them explicitly with a reason. Nothing here assumes a requirement exists beyond what the
record states.
"""

import html
import re
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from app.connectors.agency_signals import agency_post_class
from app.connectors.apify_specs import parse_date
from app.connectors.base import (
    ConnectorError,
    CostEstimate,
    DiscoveryQuery,
    DiscoveryResult,
    RawFinding,
    RawRoute,
    SignalKind,
)
from app.core.config import Settings
from app.services.routes import emails_in, role_near, route_kind
from app.services.source_check import USER_AGENT

TIMEOUT = httpx.Timeout(45.0)


def _client(transport: httpx.BaseTransport | None) -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, transport=transport)


def _pick(value: Any, prefer: tuple[str, ...] = ("eng", "ENG")) -> str | None:
    """TED returns {lang: text|[text]}; prefer English, else the first available language."""
    if isinstance(value, dict) and value:
        for lang in prefer:
            if value.get(lang):
                return _first_text(value[lang])
        return _first_text(next(iter(value.values())))
    return _first_text(value)


def _first_text(v: Any) -> str | None:
    if isinstance(v, list):
        v = v[0] if v else None
    return str(v).strip() if v not in (None, "") else None


def _day(value: Any) -> datetime | None:
    s = _first_text(value)
    return parse_date(s[:10]) if s else None


class _Free:
    is_mock = False

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings, self._transport = settings, transport

    def estimate_cost(self, query: DiscoveryQuery) -> CostEstimate:
        return CostEstimate(usd=0.0, note="free official public API")

    def _keywords(self, query: DiscoveryQuery) -> list[str]:
        return (query.keywords or self.settings.discovery_keyword_list)[:8]


# ----------------------------------------------------------------------------- TED v3
class TedApiConnector(_Free):
    name = "ted_api"
    kinds = frozenset({SignalKind.PROJECT_REQUEST})
    endpoint = "https://api.ted.europa.eu/v3/notices/search"
    fields = [
        "notice-title", "description-proc", "deadline-receipt-tender-date-lot",
        "deadline-receipt-request-date-lot", "buyer-name",
        "buyer-country", "buyer-email", "estimated-value-lot", "estimated-value-cur-lot",
        "notice-type", "publication-date",
    ]  # fmt: skip

    def build_query(self, query: DiscoveryQuery) -> str:
        since = (datetime.now(UTC) - timedelta(days=query.max_age_days)).strftime("%Y%m%d")
        terms = " OR ".join(
            f'title-proc ~ "{k}" OR description-proc ~ "{k}"' for k in self._keywords(query)
        )
        return (
            f"(notice-type = cn-standard OR notice-type = pin-cfc-standard) "
            f"AND publication-date >= {since} AND ({terms})"
        )

    def discover(self, query: DiscoveryQuery) -> DiscoveryResult:
        body = {
            "query": self.build_query(query),
            "fields": self.fields,
            "limit": min(query.max_results, 250),
            "scope": "ACTIVE",
        }
        try:
            with _client(self._transport) as c:
                resp = c.post(self.endpoint, json=body)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"TED unreachable: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise ConnectorError(f"TED returned {resp.status_code}")
        return DiscoveryResult(provider=self.name, findings=self.parse(resp.json()))

    @staticmethod
    def parse(payload: dict[str, Any]) -> list[RawFinding]:
        out: list[RawFinding] = []
        now = datetime.now(UTC)
        for n in payload.get("notices", []):
            number = n.get("publication-number")
            title = _pick(n.get("notice-title"))
            if not number or not title:
                continue
            desc = _pick(n.get("description-proc"))
            # Many notices carry the date under the "request" field (restricted/DPS procedures)
            raw_dates = (n.get("deadline-receipt-tender-date-lot") or []) + (
                n.get("deadline-receipt-request-date-lot") or []
            )
            deadlines = [d for d in (_day(x) for x in raw_dates) if d]
            deadline = max(deadlines) if deadlines else None
            status = "unknown" if deadline is None else ("open" if deadline >= now else "closed")
            vals, cur = (
                n.get("estimated-value-lot") or [],
                _first_text(n.get("estimated-value-cur-lot")),
            )
            budget = f"{vals[0]} {cur}".strip() if vals and cur else None
            email = (n.get("buyer-email") or [None])[0]
            url = f"https://ted.europa.eu/en/notice/{number}/html"
            buyer = _pick(n.get("buyer-name"))
            countries = n.get("buyer-country") or []
            routes = [RawRoute(kind="email", value=email.lower(), source_url=url)] if email else []
            out.append(
                RawFinding(
                    kind=SignalKind.PROJECT_REQUEST,
                    title=title,
                    source_url=url,
                    original_text=f"{title}\n\n{desc}" if desc else title,
                    published_at=_day(n.get("publication-date")),
                    organization_name=buyer,
                    organization_country=countries[0] if countries else None,
                    provider_confidence=0.7,
                    source_status=status,
                    deadline=deadline,
                    budget_text=budget,
                    contact_route=email.lower() if email else None,
                    routes=routes,
                )
            )
        return out


# ----------------------------------------------------------------------------- World Bank
class WorldBankApiConnector(_Free):
    name = "worldbank_api"
    kinds = frozenset({SignalKind.PROJECT_REQUEST})
    endpoint = "https://search.worldbank.org/api/v2/procnotices"
    fl = (
        "id,notice_type,notice_status,submission_deadline_date,project_name,bid_description,"
        "bid_reference_no,contact_name,contact_email,contact_organization,project_ctry_name"
    )

    def discover(self, query: DiscoveryQuery) -> DiscoveryResult:
        findings: list[RawFinding] = []
        seen: set[str] = set()
        try:
            with _client(self._transport) as c:
                for kw in self._keywords(query)[:4]:
                    resp = c.get(
                        self.endpoint,
                        params={
                            "format": "json",
                            "qterm": kw,
                            "rows": min(query.max_results, 50),
                            "fl": self.fl,
                        },
                    )
                    if resp.status_code != 200:
                        raise ConnectorError(f"World Bank returned {resp.status_code}")
                    for f in self.parse(resp.json()):
                        if f.source_url not in seen:
                            seen.add(f.source_url)
                            findings.append(f)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"World Bank unreachable: {type(exc).__name__}") from exc
        return DiscoveryResult(provider=self.name, findings=findings[: query.max_results])

    @staticmethod
    def parse(payload: dict[str, Any]) -> list[RawFinding]:
        out: list[RawFinding] = []
        now = datetime.now(UTC)
        for n in payload.get("procnotices", []):
            title = (n.get("project_name") or "").strip()
            nid = n.get("id")
            if not title or not nid:
                continue
            ntype = n.get("notice_type") or ""
            deadline = parse_date(n.get("submission_deadline_date"))
            if "award" in ntype.lower():
                status = "awarded"
            elif deadline is None:
                status = "unknown"
            else:
                status = "open" if deadline >= now else "closed"
            desc = (n.get("bid_description") or "").strip()
            url = f"https://projects.worldbank.org/en/projects-operations/procurement-detail/{nid}"
            email = (n.get("contact_email") or "").strip().lower() or None
            out.append(
                RawFinding(
                    kind=SignalKind.PROJECT_REQUEST,
                    title=title,
                    source_url=url,
                    original_text=f"{title}\n\n{desc}" if desc else title,
                    organization_name=(n.get("contact_organization") or "").strip() or None,
                    organization_country=n.get("project_ctry_name"),
                    provider_confidence=0.7,
                    source_status=status,
                    deadline=deadline,
                    contact_route=email,
                    evergreen=status == "open",  # an open deadline proves currency
                    routes=[RawRoute(kind="email", value=email, source_url=url)] if email else [],
                )
            )
        return out


# ----------------------------------------------------------------------------- UK Contracts Finder
class ContractsFinderConnector(_Free):
    name = "uk_contracts_finder"
    kinds = frozenset({SignalKind.PROJECT_REQUEST})
    endpoint = "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search"
    cpv_prefixes = ("72", "48", "73", "79")  # IT services, software, R&D, business services

    def discover(self, query: DiscoveryQuery) -> DiscoveryResult:
        since = (datetime.now(UTC) - timedelta(days=min(query.max_age_days, 30))).strftime(
            "%Y-%m-%dT00:00:00Z"
        )
        try:
            with _client(self._transport) as c:
                resp = c.get(
                    self.endpoint, params={"stages": "tender", "publishedFrom": since, "size": 100}
                )
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Contracts Finder unreachable: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise ConnectorError(f"Contracts Finder returned {resp.status_code}")
        return DiscoveryResult(
            provider=self.name,
            findings=self.parse(resp.json(), self.settings.topic_term_list)[: query.max_results],
        )

    @classmethod
    def parse(
        cls,
        payload: dict[str, Any],
        terms: list[str],
        notice_url: Any = None,
    ) -> list[RawFinding]:
        from app.services.triage import find_terms

        out: list[RawFinding] = []
        seen: set[str] = set()
        now = datetime.now(UTC)
        for rel in payload.get("releases", []):
            t = rel.get("tender") or {}
            title = (t.get("title") or "").strip()
            desc = (t.get("description") or "").replace("\r", "").strip()
            cpv = str((t.get("classification") or {}).get("id") or "")
            # cheap relevance pre-filter: IT-type CPV or an AI term; the triage still decides
            if not title or not (
                cpv.startswith(cls.cpv_prefixes) or find_terms(f"{title}\n{desc}", terms)
            ):
                continue
            doc = next((d.get("url") for d in t.get("documents", []) if d.get("url")), None)
            if not doc and notice_url is not None:
                doc = notice_url(rel)
            if not doc or doc in seen:
                continue
            seen.add(doc)
            deadline = parse_date(((t.get("tenderPeriod") or {}).get("endDate") or "")[:10])
            status = "unknown" if deadline is None else ("open" if deadline >= now else "closed")
            buyer: dict[str, Any] = next(
                (p for p in rel.get("parties", []) if "buyer" in (p.get("roles") or [])), {}
            )
            cp = buyer.get("contactPoint") or {}
            email = (cp.get("email") or "").strip().lower() or None
            val = t.get("value") or {}
            budget = (
                f"{val['amount']} {val.get('currency', '')}".strip() if val.get("amount") else None
            )
            routes = (
                [RawRoute(kind="email", value=email, source_url=doc, role=None)] if email else []
            )
            site = (buyer.get("details") or {}).get("url")
            if site and site.startswith("http"):
                routes.append(RawRoute(kind=route_kind(site), value=site, source_url=doc))
            out.append(
                RawFinding(
                    kind=SignalKind.PROJECT_REQUEST,
                    title=title,
                    source_url=doc,
                    original_text=f"{title}\n\n{desc}" if desc else title,
                    published_at=parse_date((t.get("datePublished") or "")[:10]),
                    organization_name=buyer.get("name"),
                    organization_country="GBR",
                    provider_confidence=0.7,
                    source_status=status,
                    deadline=deadline,
                    budget_text=budget,
                    contact_route=email,
                    routes=routes,
                )
            )
        return out


# ----------------------------------------------------------------------------- Hacker News
_HN = "https://hn.algolia.com/api/v1"
_SEEKING = re.compile(r"^\s*seeking\s+freelancer", re.I)


class HackerNewsFreelancerConnector(_Free):
    """Public 'Ask HN: Freelancer? Seeking freelancer?' comments that start 'SEEKING FREELANCER'.
    Each is a business stating a need for outside help, with whatever contact it chose to print."""

    name = "hn_freelancer_threads"
    kinds = frozenset({SignalKind.PROJECT_REQUEST, SignalKind.PARTNER_ACTIVITY})

    def discover(self, query: DiscoveryQuery) -> DiscoveryResult:
        findings: list[RawFinding] = []
        try:
            with _client(self._transport) as c:
                r = c.get(
                    f"{_HN}/search_by_date",
                    params={
                        "query": "Seeking freelancer",
                        "tags": "story,author_whoishiring",
                        "hitsPerPage": 10,
                    },
                )
                if r.status_code != 200:
                    raise ConnectorError(f"HN search returned {r.status_code}")
                threads = [
                    h for h in r.json().get("hits", []) if "Freelancer" in h.get("title", "")
                ][:2]
                for th in threads:
                    item = c.get(f"{_HN}/items/{th['objectID']}")
                    if item.status_code != 200:
                        raise ConnectorError(f"HN item returned {item.status_code}")
                    findings += self.parse(item.json(), self.settings.topic_term_list)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"HN unreachable: {type(exc).__name__}") from exc
        return DiscoveryResult(provider=self.name, findings=findings[: query.max_results])

    @staticmethod
    def parse(thread: dict[str, Any], terms: list[str]) -> list[RawFinding]:
        out: list[RawFinding] = []
        for c in thread.get("children", []):
            raw = c.get("text") or ""
            text = html.unescape(re.sub(r"<[^>]+>", "\n", raw.replace("<p>", "\n"))).strip()
            if not _SEEKING.match(text) or not c.get("id"):
                continue
            url = f"https://news.ycombinator.com/item?id={c['id']}"
            emails = emails_in(text)
            site = next(iter(re.findall(r"https?://[^\s)>\]\"']+", text)), None)
            kind_class = agency_post_class(text)
            out.append(
                RawFinding(
                    kind=SignalKind.PARTNER_ACTIVITY if kind_class else SignalKind.PROJECT_REQUEST,
                    title="Ask HN seeking freelancer: " + " ".join(text.split())[:110],
                    source_url=url,
                    original_text=text[:1500],
                    published_at=_parse_iso(c.get("created_at")),
                    organization_name=None,
                    provider_confidence=0.5,
                    partnership_class="active_requirement" if kind_class else None,
                    contact_route=emails[0] if emails else None,
                    routes=[
                        RawRoute(kind="email", value=e, source_url=url, role=role_near(text, e))
                        for e in emails
                    ]
                    + (
                        [RawRoute(kind=route_kind(site), value=site, source_url=url)]
                        if site
                        else []
                    ),
                )
            )
        return out


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
