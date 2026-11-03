import hashlib
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def opportunity_dedupe_key(category: str, org_key: str | None, title: str) -> str:
    raw = f"{category}|{_norm(org_key or '')}|{_norm(title)}"
    return hashlib.sha256(raw.encode()).hexdigest()


_TRACKING = re.compile(r"^(utm_.*|fbclid|gclid|mc_.*|ref|ref_src)$", re.I)


def canonical_url(url: str) -> str:
    """Normalise so the same page reached via different links/providers dedupes."""
    parts = urlsplit(url.strip())
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query) if not _TRACKING.match(k)))
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def evidence_hash(source_url: str, original_text: str) -> str:
    """Provider-independent: two providers returning the same page+text collapse to one row."""
    raw = f"{canonical_url(source_url)}|{' '.join(original_text.split())}"
    return hashlib.sha256(raw.encode()).hexdigest()


def title_similarity(a: str, b: str) -> float:
    """Jaccard similarity over normalised word sets (0..1)."""
    ta, tb = set(_norm(a).split()), set(_norm(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def is_fresh(published_at: datetime | None, max_age_days: int, now: datetime | None = None) -> bool:
    """Undated sources are NOT treated as fresh: freshness must be demonstrable."""
    if published_at is None:
        return False
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=UTC)
    now = now or datetime.now(UTC)
    return now - published_at <= timedelta(days=max_age_days)
