"""Agency/partner discovery from PERMITTED public sources. No Actor, no search engine, no login.

* PublicPartnerPageConnector  the partner/subcontracting pages of companies YOU list
* AnnouncementFeedConnector   RSS/Atom feeds YOU list (press releases, partner programme news)
* WikidataAgencyConnector     open registry (CC0) -> POTENTIAL partners only, never demand

Pages are fetched with the SSRF-safe, robots.txt-respecting checker. A page that states nothing
produces no finding: offering complementary services does not imply a need for external help.
"""

import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

import httpx

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
from app.core.logging import get_logger
from app.models.enums import SourceCheckStatus
from app.services.partnership import (
    classify_partner_text,
    find_email,
    page_title,
    visible_text,
)
from app.services.routes import routes_from_html
from app.services.source_check import USER_AGENT, SourceChecker

log = get_logger(__name__)
PARTNER_PATHS = [
    "/partners",
    "/partner-program",
    "/become-a-partner",
    "/white-label",
    "/partnerships",
]
_CONF = {"active_requirement": 0.7, "verified_model": 0.6}
_TITLE = {
    "active_requirement": "states it is looking for delivery partners / subcontractors",
    "verified_model": "publicly describes a subcontracting / white-label delivery model",
}


def _host(url: str) -> str:
    h = urlsplit(url if "//" in url else "https://" + url).hostname or ""
    return h.removeprefix("www.")


def _org_name(title: str | None, host: str) -> str:
    if title:
        part = re.split(r"\s[|\-–—:]\s", title)[
            -1 if len(re.split(r"\s[|\-–—:]\s", title)) > 1 else 0
        ]
        if 2 < len(part) < 60:
            return part.strip()
    return host


_LINK = re.compile(r"""(?is)<a\b[^>]*href=["']([^"']+)["'][^>]*>(.*?)</a>""")
_PARTNER_LINK = re.compile(r"(partner|white-?label|sub-?contract|collaborat)", re.I)
CONTACT_PATHS = ["/contact", "/contact-us", "/about/contact"]


class PartnerPageChecker:
    """Reads a domain's own partner pages (robots.txt + SSRF-safe) and reports what they STATE.
    Verification only: it never invents demand and returns nothing for silent pages."""

    def __init__(self, settings: Settings, checker: SourceChecker | None = None) -> None:
        self.settings, self._checker = settings, checker or SourceChecker()

    def urls_for(self, domain: str, page_url: str | None = None) -> list[str]:
        if page_url:
            return [page_url]
        base = domain if "//" in domain else "https://" + domain
        if urlsplit(base).path.strip("/"):
            return [base]  # an explicit page: read exactly that page
        return [base.rstrip("/") + p for p in PARTNER_PATHS]

    def check(
        self,
        domain: str,
        page_url: str | None = None,
        name: str | None = None,
        country: str | None = None,
    ) -> tuple[str, str, list[RawFinding]]:
        """Returns (status, detail, findings); status in signal | no_signal | blocked | error."""
        findings: list[RawFinding] = []
        seen_classes: set[str] = set()
        reached = blocked = 0
        urls = self.urls_for(domain, page_url)
        if not page_url:  # follow the site's own partner links first (same host only)
            urls = self._partner_links(domain) + urls
        for url in dict.fromkeys(urls):
            status, body = self._checker.fetch_page(url)
            if status is SourceCheckStatus.BLOCKED:
                blocked += 1
                continue
            if status is not SourceCheckStatus.CONFIRMED:
                continue
            reached += 1
            text = visible_text(body)
            signal = classify_partner_text(text, self.settings.topic_term_list)
            if signal is None:
                continue  # nothing stated: do not infer
            host = _host(url)
            org = name or _org_name(page_title(body), host)
            # A live page carries no date: it can show a MODEL, never prove a CURRENT request.
            cls = "verified_model"
            if cls in seen_classes:
                continue
            seen_classes.add(cls)
            routes = routes_from_html(body, url, text)
            routes += self._contact_page_routes(host)
            email = next((r.value for r in routes if r.kind == "email"), None)
            findings.append(
                RawFinding(
                    kind=SignalKind.PARTNER_ACTIVITY,
                    title=f"{org} {_TITLE[cls]}",
                    source_url=url,
                    original_text="\n".join(signal.sentences),
                    organization_name=org,
                    organization_domain=host,
                    organization_country=country,
                    provider_confidence=_CONF[cls],
                    partnership_class=cls,
                    evergreen=True,
                    source_status="unknown",
                    contact_route=email,
                    routes=routes,
                )
            )
        if findings:
            return "signal", f"{len(findings)} model statement(s)", findings
        if reached:
            return "no_signal", "pages read; nothing stated about external delivery", []
        if blocked:
            return "blocked", "robots.txt or address policy disallowed the pages", []
        root_status, _ = self._checker.fetch_page(f"https://{domain}/")
        if root_status is SourceCheckStatus.CONFIRMED:
            return "no_signal", "site reachable; no partner/subcontracting page published", []
        if root_status is SourceCheckStatus.BLOCKED:
            return "blocked", "robots.txt or address policy disallowed the site", []
        return "error", "site not reachable", []

    def _partner_links(self, domain: str) -> list[str]:
        status, body = self._checker.fetch_page(f"https://{domain}/")
        if status is not SourceCheckStatus.CONFIRMED:
            return []
        links: list[str] = []
        for href, label in _LINK.findall(body):
            text = f"{href} {re.sub(r'<[^>]+>', ' ', label)}"
            if _PARTNER_LINK.search(text):
                full = urljoin(f"https://{domain}/", href)
                if urlsplit(full).hostname in (domain, f"www.{domain}") and full not in links:
                    links.append(full)
        return links[:3]

    def _contact_page_routes(self, host: str) -> list[RawRoute]:
        for path in CONTACT_PATHS:
            url = f"https://{host}{path}"
            status, body = self._checker.fetch_page(url)
            if status is SourceCheckStatus.CONFIRMED:
                return routes_from_html(body, url, visible_text(body))
        return []


class PublicPartnerPageConnector:
    """Reads the partner pages of domains YOU list (AGENCY_SEED_URLS). Candidate domains found
    automatically are read by the candidate_verification workflow step instead."""

    name = "public_partner_pages"
    kinds = frozenset({SignalKind.PARTNER_ACTIVITY})
    is_mock = False

    def __init__(self, settings: Settings, checker: SourceChecker | None = None) -> None:
        self.settings = settings
        self._pages = PartnerPageChecker(settings, checker)

    def estimate_cost(self, query: DiscoveryQuery) -> CostEstimate:
        return CostEstimate(usd=0.0, note="free: direct fetch of listed public pages")

    def discover(self, query: DiscoveryQuery) -> DiscoveryResult:
        if not self.settings.agency_seed_list:
            raise ConnectorError(
                "AGENCY_SEED_URLS is empty: list the company domains/pages to read"
            )
        findings: list[RawFinding] = []
        for seed in self.settings.agency_seed_list[: self.settings.agency_pages_max]:
            _, _, found = self._pages.check(seed)
            findings += found
        return DiscoveryResult(provider=self.name, findings=findings[: query.max_results])


def _text(el: ElementTree.Element | None) -> str:
    return "".join(el.itertext()).strip() if el is not None else ""


class AnnouncementFeedConnector:
    name = "public_announcement_feeds"
    kinds = frozenset({SignalKind.PARTNER_ACTIVITY})
    is_mock = False

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings, self._transport = settings, transport

    def estimate_cost(self, query: DiscoveryQuery) -> CostEstimate:
        return CostEstimate(usd=0.0, note="free: public RSS/Atom feeds")

    def discover(self, query: DiscoveryQuery) -> DiscoveryResult:
        if not self.settings.agency_feed_list:
            raise ConnectorError("AGENCY_FEED_URLS is empty: list the RSS/Atom feeds to read")
        findings: list[RawFinding] = []
        checker = SourceChecker(transport=self._transport)
        for feed in self.settings.agency_feed_list:
            status, body = checker.fetch_page(feed)
            if status is not SourceCheckStatus.CONFIRMED:
                continue
            findings += self._parse(feed, body)
        return DiscoveryResult(provider=self.name, findings=findings[: query.max_results])

    def _parse(self, feed_url: str, body: str) -> list[RawFinding]:
        if "<!DOCTYPE" in body.upper() or "<!ENTITY" in body.upper():
            return []  # refuse DTD/entity tricks in untrusted XML
        try:
            root = ElementTree.fromstring(body)
        except ElementTree.ParseError:
            return []
        out: list[RawFinding] = []
        for item in list(root.iter("item")) + list(root.iter("{http://www.w3.org/2005/Atom}entry")):
            title = _text(item.find("title")) or _text(
                item.find("{http://www.w3.org/2005/Atom}title")
            )
            link_el = (
                item.find("link")
                if item.find("link") is not None
                else item.find("{http://www.w3.org/2005/Atom}link")
            )
            link = (
                link_el.get("href")
                if link_el is not None and link_el.get("href")
                else _text(link_el)
            ) or ""
            desc = (
                _text(item.find("description"))
                or _text(item.find("{http://www.w3.org/2005/Atom}summary"))
                or _text(item.find("{http://www.w3.org/2005/Atom}content"))
            )
            when = _text(item.find("pubDate")) or _text(
                item.find("{http://www.w3.org/2005/Atom}updated")
            )
            text = visible_text(f"{title}. {desc}")
            signal = classify_partner_text(text, self.settings.topic_term_list)
            if signal is None or not link:
                continue
            published = None
            try:
                published = parsedate_to_datetime(when) if when else None
            except (TypeError, ValueError):
                try:
                    published = (
                        datetime.fromisoformat(when.replace("Z", "+00:00")) if when else None
                    )
                except ValueError:
                    published = None
            if published is not None and published.tzinfo is None:
                published = published.replace(tzinfo=UTC)
            host = _host(link)
            out.append(
                RawFinding(
                    kind=SignalKind.PARTNER_ACTIVITY,
                    title=title or f"{host} announcement",
                    source_url=link,
                    original_text="\n".join(signal.sentences),
                    published_at=published,
                    organization_name=host,
                    organization_domain=host,
                    provider_confidence=_CONF[signal.partnership_class],
                    partnership_class=signal.partnership_class,
                    source_status="unknown",  # a dated post does not prove the need is still open
                    contact_route=find_email(text),
                )
            )
        return out


# Wikidata classes verified through the live API on 2026-11-03: Q1058914 "software company",
# Q1369723 "digital agency". A registry entry shows a company EXISTS, not that it needs help.
_SPARQL = """SELECT ?item ?itemLabel ?site ?countryCode WHERE {{
  VALUES ?cls {{ wd:Q1058914 wd:Q1369723 }}
  ?item wdt:P31 ?cls . ?item wdt:P856 ?site .
  ?item wdt:P17 ?country . ?country wdt:P297 ?countryCode .
  {flt}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}} LIMIT {limit}"""


class WikidataAgencyConnector:
    name = "wikidata_agencies"
    kinds = frozenset({SignalKind.PARTNER_ACTIVITY})
    is_mock = False
    endpoint = "https://query.wikidata.org/sparql"

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings, self._transport = settings, transport

    def estimate_cost(self, query: DiscoveryQuery) -> CostEstimate:
        return CostEstimate(usd=0.0, note="free: open Wikidata SPARQL endpoint")

    def discover(self, query: DiscoveryQuery) -> DiscoveryResult:
        codes = [r.upper() for r in query.regions if re.fullmatch(r"[A-Za-z]{2}", r)]
        flt = (
            "FILTER(?countryCode IN (" + ", ".join(f'"{c}"' for c in codes) + "))" if codes else ""
        )
        sparql = _SPARQL.format(flt=flt, limit=min(query.max_results, 100))
        try:
            with httpx.Client(
                headers={"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"},
                timeout=60,
                transport=self._transport,
            ) as c:
                resp = c.post(self.endpoint, data={"query": sparql})
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Wikidata unreachable: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise ConnectorError(f"Wikidata returned {resp.status_code}")
        return DiscoveryResult(provider=self.name, findings=self.parse(resp.json()))

    @staticmethod
    def parse(payload: dict[str, object]) -> list[RawFinding]:
        rows = payload.get("results", {}).get("bindings", [])  # type: ignore[attr-defined]
        out: list[RawFinding] = []
        for r in rows:
            qid = str(r["item"]["value"]).rsplit("/", 1)[-1]
            label, site, cc = r["itemLabel"]["value"], r["site"]["value"], r["countryCode"]["value"]
            if re.fullmatch(r"Q\d+", label):  # unlabeled item: no usable name
                continue
            out.append(
                RawFinding(
                    kind=SignalKind.PARTNER_ACTIVITY,
                    title=f"{label}: potential delivery partner (no demand evidence)",
                    source_url=f"https://www.wikidata.org/wiki/{qid}",
                    original_text=(
                        f"Wikidata lists {label} as a software company or digital agency in "
                        f"{cc} with official website {site}. No statement about subcontracting or "
                        "project needs."
                    ),
                    organization_name=label,
                    organization_domain=_host(site),
                    organization_country=cc,
                    provider_confidence=0.2,
                    partnership_class="potential",
                    evergreen=True,
                    contact_route=site,
                )
            )
        return out
