"""Contact-route extraction from PERMITTED public text/HTML. Nothing is guessed, constructed or
bought: a route exists only if the source prints it."""

import re
from typing import Literal
from urllib.parse import urljoin, urlsplit

from app.connectors.base import RawRoute

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_MAILTO = re.compile(r"""mailto:([^"'?\s>]+)""", re.I)
_FORM = re.compile(r"(?is)<form\b[^>]*>")
_LINK = re.compile(r"""(?is)<a\b[^>]*href=["']([^"']+)["'][^>]*>(.*?)</a>""")
_PROCUREMENT = re.compile(r"(tender|procure|bid|submit|e-?sourcing|supplier|apply)", re.I)
_JUNK_LOCAL = re.compile(r"^(noreply|no-reply|donotreply|do-not-reply)@", re.I)
_ROLE = re.compile(
    r"(head of [A-Za-z ]{2,30}|(?:chief|director|vp|manager|lead|partner(?:ship)?s?(?: manager)?"
    r"|procurement officer|contracting officer)[A-Za-z ]{0,25})",
    re.I,
)


def route_kind(value: str) -> Literal["email", "inquiry_form", "procurement_link", "page"]:
    if "@" in value and not value.lower().startswith("http"):
        return "email"
    return "procurement_link" if _PROCUREMENT.search(value) else "page"


def emails_in(text: str) -> list[str]:
    seen: list[str] = []
    for m in _EMAIL.findall(text):
        e = m.strip(".,;:)").lower()
        if not _JUNK_LOCAL.match(e) and e not in seen:
            seen.append(e)
    return seen


def role_near(text: str, email: str) -> str | None:
    """A role only if it is printed within ~80 chars of the address."""
    i = text.lower().find(email.lower())
    if i < 0:
        return None
    m = _ROLE.search(text[max(0, i - 80) : i + len(email) + 80])
    return m.group(0).strip(" ,.-:") if m else None


def routes_from_html(html: str, page_url: str, text: str) -> list[RawRoute]:
    """Emails (mailto links and printed addresses), inquiry forms, and procurement links."""
    out: list[RawRoute] = []
    found: set[str] = set()

    def add(
        kind: Literal["email", "inquiry_form", "procurement_link", "page"],
        value: str,
        role: str | None = None,
    ) -> None:
        key = f"{kind}:{value.lower()}"
        if key not in found:
            found.add(key)
            out.append(
                RawRoute(kind=kind, value=value, source_url=page_url, role=role, snippet=None)
            )

    for m in _MAILTO.findall(html):
        for e in emails_in(m):
            add("email", e, role_near(text, e))
    for e in emails_in(text):
        add("email", e, role_near(text, e))
    if _FORM.search(html):
        add("inquiry_form", page_url)
    host = urlsplit(page_url).hostname
    for href, label in _LINK.findall(html):
        full = urljoin(page_url, href)
        if urlsplit(full).scheme in ("http", "https") and _PROCUREMENT.search(
            re.sub(r"<[^>]+>", " ", label)
        ):
            if urlsplit(full).hostname == host:
                add("procurement_link", full)
    return out


# ---------------------------------------------------------------------------- route classification
# What a published route IS, and what it does NOT prove. A printed address is not evidence that its
# owner can authorise an AI project; `authority` stays "unconfirmed" unless the source says so.
_LOCAL_CLASSES: list[tuple[str, re.Pattern[str]]] = [
    ("recruitment", re.compile(r"^(careers?|jobs?|recruit\w*|hr|talent|hiring|people)\b", re.I)),
    (
        "procurement",
        re.compile(
            r"^(procure\w*|tenders?|purchas\w*|sourcing|contracts?|bids?|rfp|supplier\w*|commercial)\b",
            re.I,
        ),
    ),
    (
        "partnership",
        re.compile(r"^(partner\w*|alliances?|channel|resellers?|collaborat\w*|bd|biz\w*)\b", re.I),
    ),
    (
        "generic_inquiry",
        re.compile(
            r"^(info|contact|hello|hi|enquir\w*|inquir\w*|sales|support|office|admin|mail|team|general)\b",
            re.I,
        ),
    ),
]
_TECH_ROLE = re.compile(
    r"\b(cto|chief technology|head of (?:ai|data|engineering|technology)|vp engineering|engineering|data science|ai lead|ml lead|technical)\b",
    re.I,
)
_COMM_ROLE = re.compile(r"\b(procurement|contracting|purchasing|buyer|commercial)\b", re.I)
_PART_ROLE = re.compile(r"\bpartner(?:ship)?s?\b", re.I)


def route_class(kind: str, value: str, role: str | None) -> str:
    """procurement | recruitment | partnership | technical | generic_inquiry | named_person |
    inquiry_form | procurement_portal | page"""
    if role:
        if _TECH_ROLE.search(role):
            return "technical"
        if _COMM_ROLE.search(role):
            return "procurement"
        if _PART_ROLE.search(role):
            return "partnership"
    if kind == "inquiry_form":
        return "inquiry_form"
    if kind == "procurement_link":
        return "procurement_portal"
    if kind == "email":
        local = value.split("@", 1)[0]
        for name, rx in _LOCAL_CLASSES:
            if rx.search(local):
                return name
        return "named_person"
    return "page"


def route_authority(cls: str, opportunity_is_tender: bool) -> tuple[str, str]:
    """(authority, note). Never claims decision-making power the source does not state."""
    if cls == "procurement" and opportunity_is_tender:
        return (
            "notice_contact",
            "contact printed on this notice: right place for process questions, not proof of budget authority",
        )
    if cls == "technical":
        return (
            "likely_influencer",
            "role printed beside the address suggests technical influence; confirm who signs",
        )
    if cls == "recruitment":
        return "wrong_route", "recruitment inbox: do not use for a services engagement"
    if cls == "partnership":
        return (
            "partnership_contact",
            "partnership inbox: valid for delivery-partner conversations only",
        )
    if cls in ("generic_inquiry", "inquiry_form"):
        return "unconfirmed", "generic route: may not reach anyone who can authorise a project"
    if cls == "named_person":
        return (
            "unconfirmed",
            "a named person with no printed role: verify their role before outreach",
        )
    return "unconfirmed", "authority to commission work is not evidenced"
