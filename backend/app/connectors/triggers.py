"""Pipeline C: PROACTIVE BUYERS. Business triggers from free, official/public sources.

A trigger is NOT a request. These findings are always speculative prospects: they record an
observable fact (an organisation is hiring AI engineers; a buyer published a planning notice for
an AI procurement) so a human can decide whether a proposal is worth making. They can never be
actionable demand, and the dashboard says so.
"""

import html
import re
from typing import Any

import httpx

from app.connectors.base import (
    ConnectorError,
    DiscoveryQuery,
    DiscoveryResult,
    RawFinding,
    RawRoute,
    SignalKind,
)
from app.connectors.public_apis import (
    HackerNewsFreelancerConnector,
    TedApiConnector,
    _client,
    _parse_iso,
)
from app.services.routes import emails_in, role_near, route_kind
from app.services.triage import find_terms

_HN = "https://hn.algolia.com/api/v1"
_AI_ROLE = re.compile(
    r"\b(machine learning|ML engineer|AI engineer|LLM|applied AI|AI/ML|generative AI|NLP|"
    r"computer vision|data scientist|research engineer|head of AI|AI lead|AI researcher|prompt engineer)\b",
    re.I,
)
# A hiring post is only a credible trigger for OUTSIDE help if it signals one of these.
_EXTERNAL = re.compile(r"\b(contract(?:or|s|ing)?|freelanc\w*|consultan\w+|part-?time)\b", re.I)
_INITIATIVE = re.compile(
    r"\b(building (?:a |our )?(?:new )?(?:AI|ML|GenAI|LLM)|new (?:in-house )?(?:AI|ML|GenAI)\s*(?:unit|team|practice|lab)|"
    r"growing (?:a )?(?:GenAI|AI|ML) practice|first (?:AI|ML) hire|in-house AI|launching (?:an? )?(?:AI|GenAI))",
    re.I,
)
_SERVICE_FIRM = re.compile(
    r"\b(agency|consultancy|services company|cloud\s*&\s*AI services|systems? integrat\w+|professional services)\b",
    re.I,
)
_BAD_COMPANY = re.compile(r"^(apply|remote|onsite|hiring|full-?time|hybrid|we are|join)\b", re.I)
_CAREERS_URL = re.compile(
    r"(careers|jobs|job|apply|linkedin\.com/jobs|greenhouse|lever\.co|ashby|workable|boards\.)",
    re.I,
)
_RECRUITER_LOCAL = re.compile(r"^(jobs?|careers?|hiring|recruit\w*|talent|hr|people)@", re.I)


class HnHiringTriggerConnector:
    """Companies hiring AI/ML engineers in the monthly 'Ask HN: Who is hiring?' thread.

    Trigger: the organisation is investing in AI capability. It is NOT a project request, and
    the printed address is usually a recruiting inbox, so the route is labelled as such."""

    name = "hn_hiring_triggers"
    kinds = frozenset({SignalKind.COMPANY_SIGNAL})
    is_mock = False

    def __init__(self, settings: Any, transport: httpx.BaseTransport | None = None) -> None:
        self.settings, self._transport = settings, transport

    def estimate_cost(self, query: DiscoveryQuery):  # type: ignore[no-untyped-def]
        from app.connectors.base import CostEstimate

        return CostEstimate(usd=0.0, note="free public HN API")

    def discover(self, query: DiscoveryQuery) -> DiscoveryResult:
        try:
            with _client(self._transport) as c:
                r = c.get(
                    f"{_HN}/search_by_date",
                    params={
                        "query": "Who is hiring",
                        "tags": "story,author_whoishiring",
                        "hitsPerPage": 6,
                    },
                )
                if r.status_code != 200:
                    raise ConnectorError(f"HN search returned {r.status_code}")
                threads = [
                    h
                    for h in r.json().get("hits", [])
                    if "who is hiring" in h.get("title", "").lower()
                ]
                if not threads:
                    raise ConnectorError("no 'Who is hiring' thread found")
                item = c.get(f"{_HN}/items/{threads[0]['objectID']}")
                if item.status_code != 200:
                    raise ConnectorError(f"HN item returned {item.status_code}")
        except httpx.HTTPError as exc:
            raise ConnectorError(f"HN unreachable: {type(exc).__name__}") from exc
        found = self.parse(item.json(), self.settings.topic_term_list)
        return DiscoveryResult(provider=self.name, findings=found[: query.max_results])

    @staticmethod
    def parse(thread: dict[str, Any], terms: list[str]) -> list[RawFinding]:
        out: list[RawFinding] = []
        for c in thread.get("children", []):
            raw = c.get("text") or ""
            text = html.unescape(re.sub(r"<[^>]+>", "\n", raw.replace("<p>", "\n"))).strip()
            if not text or not c.get("id"):
                continue
            head = text.splitlines()[0]
            if not (
                _AI_ROLE.search(head) or (find_terms(text[:400], terms) and _AI_ROLE.search(text))
            ):
                continue
            company = head.split("|")[0].strip(" -–:")[:120]
            if not company or len(company) < 2 or _BAD_COMPANY.match(company):
                continue
            external, initiative = bool(_EXTERNAL.search(text)), bool(_INITIATIVE.search(text))
            service = bool(_SERVICE_FIRM.search(text))
            if not (external or initiative or service):
                continue  # ordinary full-time hiring: not a credible trigger for outside help
            url = f"https://news.ycombinator.com/item?id={c['id']}"
            emails = emails_in(text)
            site = next(
                iter(re.findall(r"https?://(?!news\.ycombinator)[^\s)>\]\"']+", text)), None
            )
            routes = [
                RawRoute(
                    kind="email",
                    value=e,
                    source_url=url,
                    role=(
                        "recruiting inbox (not a buyer contact)"
                        if _RECRUITER_LOCAL.match(e)
                        else role_near(text, e)
                    ),
                )
                for e in emails
            ]
            if site and not _CAREERS_URL.search(site):  # a careers/apply page is not a buyer route
                routes.append(RawRoute(kind=route_kind(site), value=site, source_url=url))
            out.append(
                RawFinding(
                    kind=SignalKind.PARTNER_ACTIVITY
                    if service and not external
                    else SignalKind.COMPANY_SIGNAL,
                    partnership_class="potential" if service and not external else None,
                    title=f"{company}: hiring AI/ML engineers (business trigger, not a project request)",
                    source_url=url,
                    original_text=text[:1400],
                    published_at=_parse_iso(c.get("created_at")),
                    organization_name=company,
                    organization_domain=None,
                    provider_confidence=0.3,
                    channel_hint="agency" if service and not external else "proactive_buyer",
                    contact_route=None,  # a recruiting address is not a route to a buyer
                    routes=routes,
                )
            )
        return out


class TedPlanningNoticeConnector(TedApiConnector):
    """EU prior-information notices (pin-only): a buyer announces a PLANNED AI procurement.
    A planning notice is a trigger to prepare for, not an open call for tenders."""

    name = "ted_planning_notices"
    kinds = frozenset({SignalKind.COMPANY_SIGNAL})

    def build_query(self, query: DiscoveryQuery) -> str:
        base = super().build_query(query)
        return base.replace(
            "(notice-type = cn-standard OR notice-type = pin-cfc-standard)",
            "notice-type = pin-only",
        )

    @staticmethod
    def parse(payload: dict[str, Any]) -> list[RawFinding]:
        return [_as_trigger(f) for f in TedApiConnector.parse(payload)]


def _as_trigger(f: RawFinding) -> RawFinding:
    return f.model_copy(
        update={
            "kind": SignalKind.COMPANY_SIGNAL,
            "channel_hint": "proactive_buyer",
            "source_status": "unknown",
            "provider_confidence": 0.3,
            "title": f"{f.title} (planning notice: trigger, not an open call)",
        }
    )


__all__ = [
    "HackerNewsFreelancerConnector",
    "HnHiringTriggerConnector",
    "TedPlanningNoticeConnector",
]
