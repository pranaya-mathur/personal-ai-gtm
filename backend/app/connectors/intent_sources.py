"""Intent-focused sources: they look for an actual REQUEST, then read the original page.

* CanadaBuysConnector      official open-data CSV of OPEN federal tender notices (free, keyless)
* FindATenderConnector     UK Find a Tender OCDS API (free, keyless)
* SearchIntentConnector    configurable intent queries on a search API (Brave, OPTIONAL and gated);
                           a hit is only a URL to read: demand exists only if the page itself
                           states a request, is dated, and survives triage.
"""

import csv
import io
from datetime import UTC, datetime
from typing import Any

import httpx

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
from app.connectors.public_apis import ContractsFinderConnector, _client
from app.core.config import Settings
from app.core.logging import get_logger
from app.models.enums import SourceCheckStatus
from app.services.intent import classify_intent, is_editorial, is_excluded_source, page_date
from app.services.partnership import page_title, visible_text
from app.services.routes import routes_from_html
from app.services.source_check import USER_AGENT, SourceChecker
from app.services.triage import find_terms

log = get_logger(__name__)
BRAVE_COST_PER_REQUEST = 0.005  # $5 per 1,000 requests (brave.com/search/api, 2026-11-03)


# ----------------------------------------------------------------------------- CanadaBuys
class CanadaBuysConnector:
    name = "canadabuys"
    kinds = frozenset({SignalKind.PROJECT_REQUEST})
    is_mock = False
    url = "https://canadabuys.canada.ca/opendata/pub/openTenderNotice-ouvertAvisAppelOffres.csv"

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings, self._transport = settings, transport

    def estimate_cost(self, query: DiscoveryQuery) -> CostEstimate:
        return CostEstimate(usd=0.0, note="free official open data")

    def discover(self, query: DiscoveryQuery) -> DiscoveryResult:
        try:
            with _client(self._transport) as c:
                resp = c.get(self.url)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"CanadaBuys unreachable: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise ConnectorError(f"CanadaBuys returned {resp.status_code}")
        found = self.parse(
            resp.content.decode("utf-8-sig", errors="replace"), self.settings.topic_term_list
        )
        return DiscoveryResult(provider=self.name, findings=found[: query.max_results])

    @staticmethod
    def parse(csv_text: str, terms: list[str]) -> list[RawFinding]:
        out: list[RawFinding] = []
        now = datetime.now(UTC)
        for row in csv.DictReader(io.StringIO(csv_text)):
            title = (row.get("title-titre-eng") or "").strip()
            desc = (row.get("tenderDescription-descriptionAppelOffres-eng") or "").strip()
            url = (row.get("noticeURL-URLavis-eng") or "").strip()
            if not title or not url or not find_terms(f"{title}\n{desc}", terms):
                continue
            status_txt = (row.get("tenderStatus-appelOffresStatut-eng") or "").lower()
            deadline = parse_date((row.get("tenderClosingDate-appelOffresDateCloture") or "")[:10])
            if "open" not in status_txt:
                status = "closed"
            elif deadline is not None and deadline < now:
                status = "closed"
            else:
                status = "open" if deadline is not None else "unknown"
            email = (row.get("contactInfoEmail-informationsContactCourriel") or "").strip().lower()
            org = (
                row.get("contractingEntityName-nomEntitContractante-eng")
                or row.get("procuringEntityName-nomEntitePassation-eng")
                or ""
            ).strip()
            out.append(
                RawFinding(
                    kind=SignalKind.PROJECT_REQUEST,
                    title=title,
                    source_url=url,
                    original_text=f"{title}\n\n{desc}" if desc else title,
                    published_at=parse_date(
                        (row.get("publicationDate-datePublication") or "")[:10]
                    ),
                    organization_name=org or None,
                    organization_country="CAN",
                    provider_confidence=0.7,
                    source_status=status,
                    deadline=deadline,
                    contact_route=email or None,
                    routes=[RawRoute(kind="email", value=email, source_url=url)] if email else [],
                )
            )
        return out


# ----------------------------------------------------------------------------- Find a Tender
class FindATenderConnector(ContractsFinderConnector):
    name = "uk_find_a_tender"
    endpoint = "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages"
    max_pages = 3

    def discover(self, query: DiscoveryQuery) -> DiscoveryResult:
        from datetime import timedelta

        since = (datetime.now(UTC) - timedelta(days=min(query.max_age_days, 30))).strftime(
            "%Y-%m-%dT00:00:00"
        )
        found: list[RawFinding] = []
        url: str | None = self.endpoint
        params: dict[str, Any] | None = {"updatedFrom": since, "stages": "tender", "limit": 100}
        try:
            with _client(self._transport) as c:
                for _ in range(self.max_pages):
                    if url is None:
                        break
                    resp = c.get(url, params=params)
                    if resp.status_code != 200:
                        raise ConnectorError(f"Find a Tender returned {resp.status_code}")
                    body = resp.json()
                    found += self.parse(body, self.settings.topic_term_list, self._notice_url)
                    url, params = (body.get("links") or {}).get("next"), None
                    if not url:
                        break
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Find a Tender unreachable: {type(exc).__name__}") from exc
        return DiscoveryResult(provider=self.name, findings=found[: query.max_results])

    @staticmethod
    def _notice_url(release: dict[str, Any]) -> str | None:
        nid = release.get("id")
        return f"https://www.find-tender.service.gov.uk/Notice/{nid}" if nid else None


# ----------------------------------------------------------------------------- search intent
class SearchIntentConnector:
    """Intent queries -> candidate URLs -> read each page -> keep only a stated, dated request."""

    name = "intent_search"
    kinds = frozenset({SignalKind.PROJECT_REQUEST, SignalKind.PARTNER_ACTIVITY})
    is_mock = False
    spend_family = "brave"
    endpoint = "https://api.search.brave.com/res/v1/web/search"

    def __init__(
        self,
        settings: Settings,
        transport: httpx.BaseTransport | None = None,
        checker: SourceChecker | None = None,
    ) -> None:
        self.settings, self._transport, self._checker = settings, transport, checker

    def queries(self) -> list[tuple[str, str]]:
        s = self.settings
        return [("agency", q) for q in s.intent_queries_agency_list] + [
            ("buyer", q) for q in s.intent_queries_buyer_list
        ]

    def estimate_cost(self, query: DiscoveryQuery) -> CostEstimate:
        n = len(self.queries())
        return CostEstimate(usd=round(n * BRAVE_COST_PER_REQUEST, 6), note=f"{n} search requests")

    def discover(self, query: DiscoveryQuery) -> DiscoveryResult:
        key = self.settings.brave_api_key
        if not self.settings.search_discovery_enabled or key is None:
            raise ConnectorError("intent search is off (SEARCH_DISCOVERY_ENABLED / BRAVE_API_KEY)")
        checker = self._checker or SourceChecker()
        findings: list[RawFinding] = []
        seen: set[tuple[str, str]] = set()
        requests = pages = 0
        fetched: dict[str, tuple[SourceCheckStatus, str]] = {}
        with httpx.Client(
            headers={
                "User-Agent": USER_AGENT,
                "X-Subscription-Token": key.get_secret_value(),
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(30.0),
            transport=self._transport,
        ) as c:
            for channel, q in self.queries():
                params: dict[str, Any] = {
                    "q": q,
                    "count": 20,
                    "freshness": self.settings.search_freshness,
                }
                if query.regions:
                    params["country"] = query.regions[0].upper()[:2]
                resp = c.get(self.endpoint, params=params)
                requests += 1
                if resp.status_code != 200:
                    raise ConnectorError(f"search provider returned {resp.status_code}")
                for r in resp.json().get("web", {}).get("results", []):
                    url = r.get("url", "")
                    if not url or (channel, url) in seen or is_excluded_source(url):
                        continue
                    seen.add((channel, url))
                    if url not in fetched:
                        if pages >= self.settings.search_max_pages:
                            continue
                        pages += 1
                        fetched[url] = checker.fetch_page(url)
                    f = self._read(fetched[url], url, channel, r.get("title"))
                    if f is not None:
                        findings.append(f)
        return DiscoveryResult(
            provider=self.name,
            findings=findings[: query.max_results],
            actual_cost_usd=round(requests * BRAVE_COST_PER_REQUEST, 6),
        )

    def _read(
        self, page: tuple[SourceCheckStatus, str], url: str, channel: str, hint: str | None
    ) -> RawFinding | None:
        status, body = page
        if status is not SourceCheckStatus.CONFIRMED:
            return None
        if is_editorial(page_title(body) or hint, url):
            log.info("intent_rejected", url=url, found="editorial", wanted=channel)
            return None
        text = visible_text(body)
        intent = classify_intent(text, self.settings.topic_term_list)
        wanted = "agency_seeking" if channel == "agency" else "buyer_request"
        if intent.kind != wanted:
            log.info("intent_rejected", url=url, found=intent.kind, wanted=wanted)
            return None
        host = (url.split("/")[2] if "//" in url else url).removeprefix("www.")
        routes = routes_from_html(body, url, text)
        email = next((r.value for r in routes if r.kind == "email"), None)
        title = page_title(body) or hint or host
        return RawFinding(
            kind=SignalKind.PARTNER_ACTIVITY if channel == "agency" else SignalKind.PROJECT_REQUEST,
            title=title,
            source_url=url,
            original_text="\n".join(intent.sentences),
            published_at=page_date(body),  # None = undated = stale: never treated as current
            organization_name=host,
            organization_domain=host,
            provider_confidence=0.5,
            partnership_class="active_requirement" if channel == "agency" else None,
            contact_route=email,
            routes=routes,
        )


# ----------------------------------------------------------------------------- Apify SERP intent
# Apify rejects pay-per-event runs whose maxTotalChargeUsd is below $0.50 (HTTP 400, 2026-11-03).
# The user approved this floor; the real spend is bounded by the input and the reservation guard.
PROVIDER_CAP_FLOOR_USD = 0.50
GOOGLE_ACTOR = "apify/google-search-scraper"  # $0.0045/results page, $0.00005 start (FREE tier)
BING_ACTOR = "tri_angle/bing-search-scraper"  # $0.0045/result, $0.0005 start (FREE tier)
GOOGLE_PAGE_USD, GOOGLE_START_USD = 0.0045, 0.00005
BING_RESULT_USD, BING_START_USD, BING_RESULTS_PER_PAGE = 0.0045, 0.0005, 10


class ApifySearchIntentConnector(SearchIntentConnector):
    """Intent queries through Apify SERP Actors (input/output schemas validated 2026-11-03).
    BILLABLE: runs only with a token AND APIFY_PAID_EXECUTION_APPROVED=true, each run carries a
    hard maxTotalChargeUsd, and spend is reserved against the Apify cap first. Two engines are
    used so no single search scraper is a dependency. A hit is still only a URL to read."""

    name = "apify_intent_search"
    spend_family = "apify"

    def __init__(
        self, settings: Settings, client: Any = None, checker: SourceChecker | None = None
    ) -> None:
        super().__init__(settings, None, checker)
        self._client = client

    def _engines(self) -> list[str]:
        return [e.strip() for e in self.settings.apify_search_engines.split(",") if e.strip()]

    def _plan(self) -> list[tuple[str, str, list[str]]]:
        """(engine, channel, queries) runs."""
        s = self.settings
        plan: list[tuple[str, str, list[str]]] = []
        for engine in self._engines():
            for channel, qs in (
                ("agency", s.intent_queries_agency_list),
                ("buyer", s.intent_queries_buyer_list),
            ):
                chosen = qs[: s.apify_search_bing_queries] if engine == "bing" else qs
                if chosen:
                    plan.append((engine, channel, chosen))
        return plan

    @staticmethod
    def _run_cost(engine: str, n_queries: int) -> float:
        if engine == "google":
            return GOOGLE_START_USD + n_queries * GOOGLE_PAGE_USD
        return BING_START_USD + n_queries * BING_RESULTS_PER_PAGE * BING_RESULT_USD

    def estimate_cost(self, query: DiscoveryQuery) -> CostEstimate:
        usd = sum(self._run_cost(e, len(qs)) for e, _, qs in self._plan())
        return CostEstimate(usd=round(usd, 6), note=f"{len(self._plan())} Actor runs (worst case)")

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        from app.connectors.apify_client import ApifyClient

        token = self.settings.apify_token
        if token is None or not token.get_secret_value():
            raise ConnectorError("APIFY_TOKEN is not set")
        return ApifyClient(token, self.settings.apify_api_base)

    def _input(self, engine: str, queries: list[str], query: DiscoveryQuery) -> dict[str, Any]:
        run_input: dict[str, Any] = {"queries": "\n".join(queries), "maxPagesPerQuery": 1}
        if engine == "google":
            run_input["quickDateRange"] = "m1"
            run_input["saveHtmlToKeyValueStore"] = False
            if query.regions:
                run_input["countryCode"] = query.regions[0].lower()[:2]
        return run_input

    def discover(self, query: DiscoveryQuery) -> DiscoveryResult:
        if not self.settings.apify_paid_execution_approved:
            raise ConnectorError(
                "Paid Apify execution is not approved (APIFY_PAID_EXECUTION_APPROVED)"
            )
        client = self._get_client()
        checker = self._checker or SourceChecker()
        findings: list[RawFinding] = []
        seen: set[tuple[str, str]] = set()
        fetched: dict[str, tuple[SourceCheckStatus, str]] = {}
        cost = 0.0
        pages = 0
        for engine, channel, queries in self._plan():
            actor = GOOGLE_ACTOR if engine == "google" else BING_ACTOR
            cap = max(round(self._run_cost(engine, len(queries)), 4), PROVIDER_CAP_FLOOR_USD)
            run = client.run_actor(actor, self._input(engine, queries, query), cap)
            used = run.get("usageTotalUsd")
            cost += float(used) if isinstance(used, int | float) else cap
            for item in client.dataset_items(run["defaultDatasetId"], 50):
                for r in item.get("organicResults") or []:
                    url = r.get("url") or ""
                    if not url or (channel, url) in seen or is_excluded_source(url):
                        continue
                    seen.add((channel, url))
                    if url not in fetched:
                        if pages >= self.settings.search_max_pages:
                            continue
                        pages += 1
                        fetched[url] = checker.fetch_page(url)
                    f = self._read(fetched[url], url, channel, r.get("title"))
                    if f is not None:
                        findings.append(f)
        return DiscoveryResult(
            provider=self.name,
            findings=findings[: query.max_results],
            actual_cost_usd=round(cost, 6),
        )
