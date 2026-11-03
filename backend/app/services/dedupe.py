import hashlib
import re
from datetime import UTC, datetime, timedelta


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def opportunity_dedupe_key(category: str, org_key: str | None, title: str) -> str:
    raw = f"{category}|{_norm(org_key or '')}|{_norm(title)}"
    return hashlib.sha256(raw.encode()).hexdigest()


def evidence_hash(provider: str, source_url: str, original_text: str) -> str:
    raw = f"{provider}|{source_url.strip().lower()}|{original_text.strip()}"
    return hashlib.sha256(raw.encode()).hexdigest()


def is_fresh(published_at: datetime | None, max_age_days: int, now: datetime | None = None) -> bool:
    """Undated sources are NOT treated as fresh: freshness must be demonstrable."""
    if published_at is None:
        return False
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=UTC)
    now = now or datetime.now(UTC)
    return now - published_at <= timedelta(days=max_age_days)
