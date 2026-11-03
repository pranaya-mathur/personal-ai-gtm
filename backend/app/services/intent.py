# ruff: noqa: E501
"""Commercial-intent classification for text found by search or feeds. Deterministic.

Two channels, each requiring an actual request, never generic interest:
  agency  : an agency SEEKS external delivery (subcontractors / delivery or implementation
            partners), as opposed to OFFERING its own delivery services to customers
  buyer   : a business asks for an AI solution to be built (request verb + build verb + AI term)
Everything else (offers, recruitment, news, marketplaces, directories) is not demand.
"""

import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

from app.services.triage import find_terms

_SENT = re.compile(r"(?<=[.!?])\s+|\n+")
_REQUEST = re.compile(
    r"\b(looking for|seeking|in need of|need(?:s)? (?:an?|a team|help|someone)|searching for|"
    r"request for (?:proposals?|quotes?|quotations?|information)|RFP|RFQ|invit\w+ (?:proposals|quotes|bids)|"
    r"call for (?:proposals|tenders)|want(?:s)? to (?:hire|engage|commission))\b",
    re.I,
)
_BUILD = re.compile(
    r"\b(build|develop(?:ment|ing)?|implement\w*|integrat\w*|deploy\w*|design|create|automate|"
    r"prototype|pilot|MVP|proof of concept)\b",
    re.I,
)
_EXTERNAL = re.compile(
    r"\b(sub-?contractors?|delivery partners?|implementation partners?|technology partners?|"
    r"development partners?|engineering partners?|white-?label (?:partners?|teams?|developers?)|"
    r"external (?:developers|engineers|specialists|team)|specialist (?:partners?|engineers)|"
    r"freelance (?:developers?|engineers?)|agency partners?)\b",
    re.I,
)
_OFFER = re.compile(
    r"\b(we (?:offer|provide|deliver|specialize|help|build|develop)\b|our (?:services|team of experts)|"
    r"hire us|get a (?:free )?quote|book a (?:free )?(?:call|demo)|trusted by|"
    r"white-?label (?:ai )?(?:solutions?|services?|platform))\b",
    re.I,
)
_RECRUIT = re.compile(
    r"\b(we(?:'re| are) hiring|job (?:opening|description)|full-?time|part-?time|salary|"
    r"apply (?:now|today) (?:for|to join)|vacanc\w+|recruit\w*|join our team|years of experience)\b",
    re.I,
)
EXCLUDED_DOMAINS = re.compile(
    r"(^|\.)(upwork|freelancer|fiverr|toptal|guru|peopleperhour|99designs|truelancer|contra|malt|"
    r"codementor|catalant|clutch|goodfirms|designrush|sortlist|linkedin|glassdoor|indeed|"
    r"ziprecruiter|remoteok|weworkremotely|wellfound|angel|monster|reddit|facebook|twitter|x|"
    r"quora|youtube|medium|pinterest|wikipedia)\.[a-z.]+$",
    re.I,
)


def is_excluded_source(url: str) -> bool:
    """Freelance marketplaces, job boards, directories and social platforms are never sources."""
    host = (urlsplit(url).hostname or "").lower()
    return bool(EXCLUDED_DOMAINS.search(host))


@dataclass
class Intent:
    kind: str  # agency_seeking | agency_offering | buyer_request | recruitment | none
    sentences: list[str]


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT.split(text) if 25 <= len(s.strip()) <= 400]


def classify_intent(text: str, ai_terms: list[str]) -> Intent:
    sents = _sentences(text)
    ai = bool(find_terms(text, ai_terms))
    if _RECRUIT.search(text[:600]) and not _EXTERNAL.search(text):
        return Intent("recruitment", [])
    seeking = [s for s in sents if _REQUEST.search(s) and _EXTERNAL.search(s)]
    if seeking and ai:
        return Intent("agency_seeking", seeking[:3])
    buyer = [
        s for s in sents if _REQUEST.search(s) and _BUILD.search(s) and find_terms(s, ai_terms)
    ]
    if buyer:
        return Intent("buyer_request", buyer[:3])
    if _OFFER.search(text) and not seeking:
        return Intent("agency_offering", [])
    return Intent("none", [])


_META_DATE = re.compile(
    r"""(?is)<meta[^>]+(?:property|name|itemprop)=["'](?:article:published_time|datePublished|date|pubdate|og:published_time)["'][^>]+content=["']([^"']+)["']"""
)
_LD_DATE = re.compile(r'"datePublished"\s*:\s*"([^"]+)"')
_TIME = re.compile(r"""(?is)<time[^>]+datetime=["']([^"']+)["']""")


def page_date(page: str) -> datetime | None:
    """Publication date only from machine-readable markup the page itself provides."""
    for rx in (_META_DATE, _LD_DATE, _TIME):
        m = rx.search(page)
        if m:
            raw = html.unescape(m.group(1)).strip()
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00")[:32])
            except ValueError:
                try:
                    dt = datetime.strptime(raw[:10], "%Y-%m-%d")
                except ValueError:
                    continue
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    return None
