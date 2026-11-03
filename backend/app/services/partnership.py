# ruff: noqa: E501
"""Deterministic classification of agency/partner text. Never infers a need.

  active_requirement  the text itself says the company is looking for subcontractors / delivery
                      or implementation partners (and mentions AI work)
  verified_model      the text states an existing subcontracting / white-label / partner-delivery
                      model (no current project implied)
  None                nothing stated -> no finding is produced

Offering complementary services, or having a "partners" page that is a reseller programme, is NOT
evidence that a company needs external engineers.
"""

import html
import re
from dataclasses import dataclass

from app.services.triage import find_terms

_SENT = re.compile(r"(?<=[.!?])\s+|\n+")
_SEEK = re.compile(
    r"\b(we(?:'re| are)?\s+(?:currently\s+|now\s+)?(?:looking for|seeking|searching for|recruiting|"
    r"in need of|hiring)|looking for|seeking|call for|request for)\b",
    re.I,
)
_PARTNER_NOUN = re.compile(
    r"\b(sub-?contractors?|delivery partners?|implementation partners?|technology partners?|"
    r"development partners?|engineering partners?|ai partners?|white-?label partners?|"
    r"external (?:developers|engineers|specialists)|specialist partners?)\b",
    re.I,
)
_MODEL = re.compile(
    r"\b(white[- ]?label (?:partners?|teams?)|sub-?contracted (?:team|developers|engineers|partners)|we (?:use|hire|engage|rely on) (?:\w+ ){0,2}sub-?contractors|we (?:work|partner|collaborate) with "
    r"(?:specialist |trusted |independent )?(?:sub-?contractors|freelance \w+|partner agencies|"
    r"delivery partners|specialist partners)|our network of (?:specialist |trusted )?"
    r"(?:partners|developers|engineers|sub-?contractors)|trusted (?:delivery )?partners? deliver|"
    r"outsourc(?:e|ed|ing) (?:development|engineering|delivery))",
    re.I,
)


@dataclass
class PartnerSignal:
    partnership_class: str  # active_requirement | verified_model
    sentences: list[str]  # verbatim from the source
    ai_terms: list[str]


def visible_text(page: str) -> str:
    page = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", page)
    page = re.sub(r"(?s)<br\s*/?>|</p>|</li>|</h\d>|</div>", "\n", page)
    text = html.unescape(re.sub(r"(?s)<[^>]+>", " ", page))
    return "\n".join(" ".join(line.split()) for line in text.splitlines() if line.strip())


def page_title(page: str) -> str | None:
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", page)
    return " ".join(html.unescape(m.group(1)).split()) if m else None


def classify_partner_text(text: str, ai_terms: list[str]) -> PartnerSignal | None:
    sentences = [s.strip() for s in _SENT.split(text) if 20 <= len(s.strip()) <= 400]
    active = [s for s in sentences if _SEEK.search(s) and _PARTNER_NOUN.search(s)]
    model = [s for s in sentences if _MODEL.search(s)]
    ai = [t for t, _ in find_terms(text, ai_terms)]
    if active and ai:
        return PartnerSignal("active_requirement", active[:3], ai)
    if active or model:
        return PartnerSignal("verified_model", (active + model)[:3], ai)
    return None


_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def find_email(text: str) -> str | None:
    """A contact route only if the source literally prints one; never constructed or guessed."""
    m = _EMAIL.search(text)
    return m.group(0) if m else None
