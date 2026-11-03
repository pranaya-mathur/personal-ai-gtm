"""Bearer-token authentication with viewer < operator < admin roles.

Tokens come from API_TOKENS ("name:role:token,..."), never from the repo. The authenticated
`name` is what gets recorded as approver/verifier; clients can no longer claim an identity in a
request body. With no tokens configured the API refuses everything except /health (fail closed);
the only bypass is the explicit AUTH_DISABLED=true development flag.
"""

import hmac
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request

from app.core.config import Settings, get_settings

ROLES = {"viewer": 1, "operator": 2, "admin": 3}
_WINDOW_SECONDS = 60.0
_hits: dict[str, deque[float]] = defaultdict(deque)


@dataclass(frozen=True)
class Identity:
    name: str
    role: str


def parse_tokens(raw: str) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        name, _, rest = part.partition(":")
        role, _, token = rest.partition(":")
        if not name or role not in ROLES or len(token) < 16:
            raise ValueError("API_TOKENS entries must be name:role:token with a >=16 char token")
        out.append((name, role, token))
    return out


def _limit(identity: Identity, mutating: bool, settings: Settings) -> None:
    cap = settings.api_mutation_limit_per_minute if mutating else settings.api_rate_limit_per_minute
    key = f"{identity.name}:{'w' if mutating else 'r'}"
    now = time.monotonic()
    q = _hits[key]
    while q and now - q[0] > _WINDOW_SECONDS:
        q.popleft()
    if len(q) >= cap:
        raise HTTPException(429, "rate limit exceeded; slow down", headers={"Retry-After": "30"})
    q.append(now)


def reset_rate_limits() -> None:
    _hits.clear()


def require_role(minimum: str):  # type: ignore[no-untyped-def]
    def dependency(
        request: Request,
        authorization: str | None = Header(default=None),
        settings: Settings = Depends(get_settings),
    ) -> Identity:
        cached: Identity | None = getattr(request.state, "identity", None)
        if cached is not None:  # authenticated and rate-counted earlier in this request
            if ROLES[cached.role] < ROLES[minimum]:
                raise HTTPException(403, f"{minimum} role required")
            return cached
        mutating = request.method not in ("GET", "HEAD", "OPTIONS")
        if settings.auth_disabled:
            request.state.identity = Identity("dev-local", "admin")
            return request.state.identity  # type: ignore[no-any-return]
        tokens = parse_tokens(settings.api_tokens)
        if not tokens:
            raise HTTPException(503, "authentication is not configured (set API_TOKENS)")
        presented = (authorization or "").removeprefix("Bearer ").strip()
        match: Identity | None = None
        for name, role, token in tokens:  # constant-time comparison against every token
            if presented and hmac.compare_digest(presented.encode(), token.encode()):
                match = Identity(name, role)
        if match is None:
            raise HTTPException(
                401, "invalid or missing bearer token", headers={"WWW-Authenticate": "Bearer"}
            )
        _limit(match, mutating, settings)
        request.state.identity = match
        if ROLES[match.role] < ROLES[minimum]:
            raise HTTPException(403, f"{minimum} role required")
        return match

    return dependency
