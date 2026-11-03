"""Candidate organizations found by automatic SOURCE DISCOVERY. A candidate is a lead to read,
never evidence: it only becomes an opportunity if its own pages state something."""

import re
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlsplit

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import SourceCandidate
from app.utils import utcnow

_SKIP = re.compile(
    r"(^|\.)(github\.com|githubusercontent\.com|twitter\.com|x\.com|linkedin\.com|facebook\.com|"
    r"medium\.com|youtube\.com|wikipedia\.org|wikidata\.org|upwork\.com|freelancer\.com|"
    r"fiverr\.com|toptal\.com)$",
    re.I,
)


@dataclass(frozen=True)
class Candidate:
    domain: str
    name: str | None = None
    country: str | None = None
    origin: str = "unknown"
    origin_ref: str | None = None
    page_url: str | None = None


def normalize_domain(value: str) -> str | None:
    """Official-domain normalisation. Marketplaces and social profiles are never candidates."""
    v = value.strip()
    if not v:
        return None
    host = (
        (urlsplit(v if "//" in v else "https://" + v).hostname or "").lower().removeprefix("www.")
    )
    if "." not in host or _SKIP.search(host):
        return None
    return host


def upsert_candidate(session: Session, c: Candidate) -> bool:
    """Returns True if a NEW candidate was stored (False = already known)."""
    domain = normalize_domain(c.domain)
    if domain is None:
        return False
    exists = session.execute(
        select(SourceCandidate.id).where(SourceCandidate.domain == domain)
    ).first()
    if exists:
        return False
    session.add(
        SourceCandidate(
            domain=domain,
            name=c.name,
            country=c.country,
            origin=c.origin,
            origin_ref=c.origin_ref,
            page_url=c.page_url,
        )
    )
    session.flush()
    return True


def known_count(session: Session, origin: str) -> int:
    return int(
        session.execute(
            select(func.count())
            .select_from(SourceCandidate)
            .where(SourceCandidate.origin == origin)
        ).scalar_one()
    )


def next_batch(session: Session, limit: int) -> list[SourceCandidate]:
    """Unchecked candidates first; errors are retried after a day."""
    retry_after = utcnow() - timedelta(days=1)
    return list(
        session.execute(
            select(SourceCandidate)
            .where(
                or_(
                    SourceCandidate.status == "new",
                    (SourceCandidate.status == "error")
                    & (SourceCandidate.last_checked_at < retry_after),
                )
            )
            .order_by(SourceCandidate.created_at)
            .limit(limit)
        ).scalars()
    )
