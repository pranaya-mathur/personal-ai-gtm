"""Automatic SOURCE DISCOVERY: find new organizations/pages worth reading, from permitted sources.
Discoverers never create findings or opportunities; evidence comes only from reading the pages."""

import re
from typing import Any, Protocol

import httpx

from app.connectors.base import ConnectorError, DiscoveryQuery
from app.core.config import Settings
from app.services.candidates import Candidate, normalize_domain
from app.services.source_check import USER_AGENT

TIMEOUT = httpx.Timeout(45.0)


def _HOST(h: dict[str, str]) -> dict[str, str]:  # noqa: N802
    return {"User-Agent": USER_AGENT, **h}


# Worldwide discovery, starting with the priority markets; others come via query.regions.
PRIORITY_MARKETS = ["US", "GB", "DE", "FR", "AE", "SA", "CA", "AU", "SG", "IN"]


class SourceDiscoverer(Protocol):
    name: str

    def discover(self, query: DiscoveryQuery, already_known: int) -> list[Candidate]: ...


class WikidataDiscoverer:
    """Software companies / digital agencies with an official website (Wikidata, CC0). Pages
    through the registry with OFFSET so each run finds organizations not seen before."""

    name = "wikidata"
    endpoint = "https://query.wikidata.org/sparql"

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings, self._transport = settings, transport

    def discover(self, query: DiscoveryQuery, already_known: int) -> list[Candidate]:
        codes = [r.upper() for r in query.regions if re.fullmatch(r"[A-Za-z]{2}", r)]
        step = self.settings.candidates_per_run * 2
        offset = already_known
        if not codes:  # an unfiltered, sorted scan times out: rotate through priority markets
            slot = already_known // step
            codes = [PRIORITY_MARKETS[slot % len(PRIORITY_MARKETS)]]
            offset = (slot // len(PRIORITY_MARKETS)) * step
        flt = "FILTER(?countryCode IN (" + ", ".join(f'"{c}"' for c in codes) + "))"
        sparql = (
            "SELECT ?item ?itemLabel ?site ?countryCode WHERE { "
            "VALUES ?cls { wd:Q1058914 wd:Q1369723 } ?item wdt:P31 ?cls . ?item wdt:P856 ?site . "
            "?item wdt:P17 ?country . ?country wdt:P297 ?countryCode . " + flt + " "
            'SERVICE wikibase:label { bd:serviceParam wikibase:language "en". } } '
            f"LIMIT {step} OFFSET {offset}"
        )
        try:
            with httpx.Client(
                headers=_HOST({"Accept": "application/sparql-results+json"}),
                timeout=60,
                transport=self._transport,
            ) as c:
                resp = c.post(self.endpoint, data={"query": sparql})
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Wikidata unreachable: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise ConnectorError(f"Wikidata returned {resp.status_code}")
        out: list[Candidate] = []
        for r in resp.json().get("results", {}).get("bindings", []):
            label = r["itemLabel"]["value"]
            domain = normalize_domain(r["site"]["value"])
            if domain and not re.fullmatch(r"Q\d+", label):
                out.append(
                    Candidate(
                        domain=domain,
                        name=label,
                        country=r["countryCode"]["value"],
                        origin=self.name,
                        origin_ref="https://www.wikidata.org/wiki/"
                        + r["item"]["value"].rsplit("/", 1)[-1],
                    )
                )
        return out


class GitHubOrgDiscoverer:
    """Organizations that describe themselves as AI/software agencies on GitHub (official API,
    unauthenticated limits respected). Only the org's self-published website is kept."""

    name = "github"
    api = "https://api.github.com"
    queries = [
        "ai agency",
        "ai consulting",
        "software consultancy",
        "digital agency",
        "ml consulting",
    ]

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings, self._transport = settings, transport

    def discover(self, query: DiscoveryQuery, already_known: int) -> list[Candidate]:
        cap = min(self.settings.candidates_per_run, 10)  # 60 core requests/hour unauthenticated
        q = self.queries[(already_known // 30) % len(self.queries)]
        page = 1 + (already_known // 30) // len(self.queries)
        out: list[Candidate] = []
        try:
            with httpx.Client(
                headers=_HOST({"Accept": "application/vnd.github+json"}),
                timeout=TIMEOUT,
                transport=self._transport,
            ) as c:
                resp = c.get(
                    f"{self.api}/search/users",
                    params={"q": f"{q} type:org", "per_page": 30, "page": page},
                )
                if resp.status_code != 200:
                    raise ConnectorError(f"GitHub search returned {resp.status_code}")
                for item in resp.json().get("items", [])[already_known % 30 :]:
                    if len(out) >= cap:
                        break
                    org = c.get(f"{self.api}/orgs/{item['login']}")
                    if org.status_code != 200:
                        break  # rate limited or gone: stop politely
                    d: dict[str, Any] = org.json()
                    domain = normalize_domain(d.get("blog") or "")
                    if domain:
                        out.append(
                            Candidate(
                                domain=domain,
                                name=d.get("name") or d.get("login"),
                                country=d.get("location"),
                                origin=self.name,
                                origin_ref=d.get("html_url"),
                            )
                        )
        except httpx.HTTPError as exc:
            raise ConnectorError(f"GitHub unreachable: {type(exc).__name__}") from exc
        return out


class BraveSearchDiscoverer:
    """Search-provider discovery (Brave Search API: GET /res/v1/web/search, header
    X-Subscription-Token, results at web.results[].url/title/description; verified in the docs
    2026-11-03). Disabled unless SEARCH_DISCOVERY_ENABLED=true AND BRAVE_API_KEY is set. Results
    are only URLs to READ; a search hit is never evidence of demand."""

    name = "search"
    endpoint = "https://api.search.brave.com/res/v1/web/search"
    queries = [
        '"looking for" "white-label" AI development partner agency',
        '"we are seeking" subcontractors AI engineering agency partner',
        '"become a partner" AI implementation agency subcontract',
    ]

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings, self._transport = settings, transport

    def discover(self, query: DiscoveryQuery, already_known: int) -> list[Candidate]:
        key = self.settings.brave_api_key
        if not self.settings.search_discovery_enabled or key is None:
            raise ConnectorError(
                "search discovery is off (SEARCH_DISCOVERY_ENABLED / BRAVE_API_KEY)"
            )
        out: list[Candidate] = []
        with httpx.Client(
            headers=_HOST(
                {"X-Subscription-Token": key.get_secret_value(), "Accept": "application/json"}
            ),
            timeout=TIMEOUT,
            transport=self._transport,
        ) as c:
            for q in self.queries[: max(1, self.settings.candidates_per_run // 10)]:
                params: dict[str, Any] = {"q": q, "count": 20}
                if query.regions:
                    params["country"] = query.regions[0].upper()[:2]
                resp = c.get(self.endpoint, params=params)
                if resp.status_code != 200:
                    raise ConnectorError(f"search provider returned {resp.status_code}")
                for r in resp.json().get("web", {}).get("results", []):
                    domain = normalize_domain(r.get("url", ""))
                    if domain:
                        out.append(
                            Candidate(domain, r.get("title"), None, self.name, q, page_url=r["url"])
                        )
        return out


def build_discoverers(settings: Settings) -> list[SourceDiscoverer]:
    registry: dict[str, Any] = {
        "wikidata": WikidataDiscoverer,
        "github": GitHubOrgDiscoverer,
        "search": BraveSearchDiscoverer,
    }
    unknown = [n for n in settings.enabled_discoverer_list if n not in registry]
    if unknown:
        raise ValueError(f"Unknown discoverers: {unknown}. Known: {sorted(registry)}")
    return [registry[n](settings) for n in settings.enabled_discoverer_list]
