"""Original-source verification.

Fetches the exact URL cited by a finding and checks that the claimed title text really appears
on the page. Safety: http(s) only, no private/loopback/link-local targets (SSRF), manual
redirect handling with re-validation, robots.txt respected, size/time capped, one request per
page with an identifying User-Agent. This is NOT human verification and never sets `verified`.
"""

import html
import ipaddress
import re
import socket
from collections.abc import Callable
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from app.models.enums import SourceCheckStatus

USER_AGENT = "gtm-command-center-source-check/0.1 (+evidence verification; respects robots.txt)"
MAX_BYTES = 2_000_000
MAX_REDIRECTS = 3


def _default_resolver(host: str) -> list[str]:
    return [str(info[4][0]) for info in socket.getaddrinfo(host, None)]


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", text.lower()).split())


def _visible_text(page: str) -> str:
    page = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", page)
    return html.unescape(re.sub(r"(?s)<[^>]+>", " ", page))


class SourceChecker:
    def __init__(
        self,
        transport: httpx.BaseTransport | None = None,
        resolver: Callable[[str], list[str]] = _default_resolver,
    ) -> None:
        self._http = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=httpx.Timeout(15.0),
            follow_redirects=False,
            transport=transport,
        )
        self._resolver = resolver

    def _url_is_safe(self, url: str) -> bool:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            return False
        try:
            addrs = self._resolver(parts.hostname)
        except OSError:
            return False
        if not addrs:
            return False
        for a in addrs:
            ip = ipaddress.ip_address(a)
            if not ip.is_global:
                return False
        return True

    def _robots_allows(self, url: str) -> bool:
        parts = urlsplit(url)
        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        try:
            resp = self._http.get(robots_url)
        except httpx.HTTPError:
            return False  # cannot establish permission => do not fetch
        if resp.status_code >= 500:
            return False
        if resp.status_code >= 400:
            return True  # no robots file => allowed (RFC 9309)
        rp = RobotFileParser()
        rp.parse(resp.text.splitlines())
        return rp.can_fetch(USER_AGENT, url)

    def fetch_page(self, url: str) -> tuple[SourceCheckStatus, str]:
        """Same safety rules as `check` (SSRF guard, robots.txt, redirects re-validated, size cap).
        Returns (CONFIRMED + raw body) on success, otherwise (BLOCKED/UNREACHABLE, "")."""
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            if not self._url_is_safe(current) or not self._robots_allows(current):
                return SourceCheckStatus.BLOCKED, ""
            try:
                resp = self._http.get(current)
            except httpx.HTTPError:
                return SourceCheckStatus.UNREACHABLE, ""
            if resp.status_code in {301, 302, 303, 307, 308} and "location" in resp.headers:
                current = urljoin(current, resp.headers["location"])
                continue
            if resp.status_code >= 400:
                return SourceCheckStatus.UNREACHABLE, ""
            return SourceCheckStatus.CONFIRMED, resp.text[:MAX_BYTES]
        return SourceCheckStatus.UNREACHABLE, ""

    def check(self, url: str, claim_text: str) -> SourceCheckStatus:
        claim = _norm(claim_text.split("\n", 1)[0])
        if not claim:
            return SourceCheckStatus.MISMATCH
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            if not self._url_is_safe(current) or not self._robots_allows(current):
                return SourceCheckStatus.BLOCKED
            try:
                resp = self._http.get(current)
            except httpx.HTTPError:
                return SourceCheckStatus.UNREACHABLE
            if resp.status_code in {301, 302, 303, 307, 308} and "location" in resp.headers:
                current = urljoin(current, resp.headers["location"])
                continue
            if resp.status_code >= 400:
                return SourceCheckStatus.UNREACHABLE
            body = resp.text[:MAX_BYTES]
            found = claim in _norm(_visible_text(body))
            return SourceCheckStatus.CONFIRMED if found else SourceCheckStatus.MISMATCH
        return SourceCheckStatus.UNREACHABLE
