"""SAM.gov Get Opportunities Public API (official, free with public API key).

Docs: https://open.gsa.gov/api/get-opportunities-public-api/
Endpoint: GET https://api.sam.gov/opportunities/v2/search
Requires SAMGOV_API_KEY. Nothing runs without the key. Rate limits are the account's
(basic ≈ 10/day); we keep pages small and keyword-filtered.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.connectors.apify_specs import parse_date
from app.connectors.base import (
    ConnectorDisabled,
    ConnectorError,
    CostEstimate,
    DiscoveryQuery,
    DiscoveryResult,
    RawFinding,
    RawRoute,
    SignalKind,
)
from app.connectors.public_apis import _client
from app.core.config import Settings
from app.services.triage import find_terms


class SamGovConnector:
    name = "sam_gov"
    kinds = frozenset({SignalKind.PROJECT_REQUEST})
    is_mock = False
    endpoint = "https://api.sam.gov/opportunities/v2/search"
    # Solicitations + combined synopsis; exclude awards by default.
    ptypes = "o,k,r"

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings, self._transport = settings, transport

    def estimate_cost(self, query: DiscoveryQuery) -> CostEstimate:
        return CostEstimate(usd=0.0, note="free official SAM.gov public API (key required)")

    def discover(self, query: DiscoveryQuery) -> DiscoveryResult:
        key = self.settings.samgov_api_key
        if key is None or not key.get_secret_value():
            raise ConnectorDisabled("SAM.gov is off (SAMGOV_API_KEY not set)")
        now = datetime.now(UTC)
        posted_from = (now - timedelta(days=min(query.max_age_days, 90))).strftime("%m/%d/%Y")
        posted_to = now.strftime("%m/%d/%Y")
        keywords = (query.keywords or self.settings.discovery_keyword_list)[:5]
        findings: list[RawFinding] = []
        seen: set[str] = set()
        try:
            with _client(self._transport) as c:
                for kw in keywords:
                    resp = c.get(
                        self.endpoint,
                        params={
                            "api_key": key.get_secret_value(),
                            "postedFrom": posted_from,
                            "postedTo": posted_to,
                            "ptype": self.ptypes,
                            "title": kw,
                            "limit": min(query.max_results, 100),
                            "offset": 0,
                        },
                    )
                    if resp.status_code == 401:
                        raise ConnectorError("SAM.gov rejected the API key")
                    if resp.status_code == 429:
                        raise ConnectorError("SAM.gov rate limit reached")
                    if resp.status_code != 200:
                        raise ConnectorError(f"SAM.gov returned {resp.status_code}")
                    for f in self.parse(resp.json(), self.settings.topic_term_list):
                        if f.source_url not in seen:
                            seen.add(f.source_url)
                            findings.append(f)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"SAM.gov unreachable: {type(exc).__name__}") from exc
        return DiscoveryResult(provider=self.name, findings=findings[: query.max_results])

    @staticmethod
    def parse(payload: dict[str, Any], terms: list[str]) -> list[RawFinding]:
        out: list[RawFinding] = []
        now = datetime.now(UTC)
        for n in payload.get("opportunitiesData") or []:
            title = (n.get("title") or "").strip()
            notice_id = n.get("noticeId") or n.get("solicitationNumber")
            if not title or not notice_id:
                continue
            desc = (n.get("description") or n.get("additionalInfoLink") or "").strip()
            # description may be HTML; keep a short plain slice for triage
            if "<" in desc:
                desc = desc[:2000]
            blob = f"{title}\n{desc}"
            if terms and not find_terms(blob, terms):
                # title filter is soft; drop clearly off-topic if topic terms configured
                if not find_terms(title, terms):
                    continue
            deadline = parse_date((n.get("responseDeadLine") or "")[:10]) or parse_date(
                (n.get("responseDeadLine") or "")[:19]
            )
            # SAM sometimes returns MM/dd/yyyy HH:mm:ss
            if deadline is None and n.get("responseDeadLine"):
                raw = str(n["responseDeadLine"]).strip()
                for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y"):
                    try:
                        deadline = datetime.strptime(raw[:19], fmt).replace(tzinfo=UTC)
                        break
                    except ValueError:
                        continue
            active = (n.get("active") or "").lower()
            if active == "no":
                status = "closed"
            elif deadline is not None:
                status = "open" if deadline >= now else "closed"
            else:
                status = "unknown"
            ui_link = (n.get("uiLink") or "").strip()
            url = ui_link or f"https://sam.gov/opp/{notice_id}/view"
            org = (
                (n.get("fullParentPathName") or n.get("organizationName") or "")
                .split(".")[-1]
                .strip()
            )
            email = None
            point = n.get("pointOfContact") or []
            if isinstance(point, list) and point:
                email = (point[0].get("email") or "").strip().lower() or None
            published = parse_date((n.get("postedDate") or "")[:10])
            out.append(
                RawFinding(
                    kind=SignalKind.PROJECT_REQUEST,
                    title=title[:500],
                    source_url=url,
                    original_text=blob[:4000],
                    published_at=published,
                    organization_name=org or None,
                    organization_country="USA",
                    provider_confidence=0.7,
                    source_status=status,
                    deadline=deadline,
                    contact_route=email,
                    routes=[RawRoute(kind="email", value=email, source_url=url)] if email else [],
                )
            )
        return out
