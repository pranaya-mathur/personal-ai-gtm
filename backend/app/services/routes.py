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
