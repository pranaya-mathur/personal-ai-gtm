"""Provider-neutral discovery contract.

Business logic depends ONLY on these types. Concrete providers (Apify Actors, MCP servers,
official APIs, permitted public pages) live behind `DiscoveryConnector` and are selected by
configuration, so a provider can be swapped without touching agents or the orchestrator.
"""

import enum
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class SignalKind(enum.StrEnum):
    """What the *source itself* asserts. The classifier maps this to an OpportunityCategory."""

    PROJECT_REQUEST = "project_request"  # a buyer states a need (-> confirmed demand)
    PARTNER_ACTIVITY = "partner_activity"  # an agency publicly shows delivery/partner activity
    COMPANY_SIGNAL = "company_signal"  # indirect signal, no stated need (-> speculative)


class DiscoveryQuery(BaseModel):
    keywords: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    max_results: int = Field(default=25, ge=1, le=500)
    max_age_days: int = Field(default=90, ge=1)
    connector_names: list[str] | None = None  # restrict a run to specific providers


class RawContact(BaseModel):
    """Only populated when the source literally contains it. Never inferred."""

    full_name: str
    title: str | None = None
    email: str | None = None
    profile_url: str | None = None


class RawRoute(BaseModel):
    """A published way to reach the buyer/partner. Only values the source literally contains."""

    kind: Literal["email", "inquiry_form", "procurement_link", "page"]
    value: str
    source_url: str
    role: str | None = None
    snippet: str | None = None


class RawFinding(BaseModel):
    kind: SignalKind
    title: str
    source_url: str
    original_text: str  # verbatim source excerpt; preserved as evidence
    published_at: datetime | None = None
    organization_name: str | None = None
    organization_domain: str | None = None
    organization_country: str | None = None
    contacts: list[RawContact] = Field(default_factory=list)
    provider_confidence: float = Field(default=0.5, ge=0, le=1)
    # Structured facts, only when the provider record states them.
    source_status: Literal["open", "closed", "awarded", "unknown"] = "unknown"
    deadline: datetime | None = None
    budget_text: str | None = None
    contact_route: str | None = None  # buyer email / application URL printed in the source
    # Agency findings: which of the three partnership classes the SOURCE supports.
    routes: list[RawRoute] = Field(default_factory=list)
    channel_hint: str | None = None  # e.g. "proactive_buyer" (a trigger, never a request)
    evergreen: bool = False  # live page with no publication date: treated as current, stays undated
    partnership_class: Literal["active_requirement", "verified_model", "potential"] | None = None


class CostEstimate(BaseModel):
    usd: float = Field(ge=0)
    note: str = ""


class DiscoveryResult(BaseModel):
    provider: str
    findings: list[RawFinding]
    actual_cost_usd: float = 0.0


@runtime_checkable
class DiscoveryConnector(Protocol):
    name: str
    kinds: frozenset[SignalKind]  # which signal kinds this provider can produce
    is_mock: bool

    def estimate_cost(self, query: DiscoveryQuery) -> CostEstimate:
        """Upper-bound cost BEFORE any paid call. The spend guard enforces limits on this."""
        ...

    def discover(self, query: DiscoveryQuery) -> DiscoveryResult: ...


class ConnectorError(Exception):
    """Raised by connectors for provider failures (retried by the runner)."""


class ConnectorRejected(ConnectorError):
    """The provider refused the request before doing any work (4xx on submit). Nothing was
    billed, so any reserved spend is released and the call is not retried."""


class ProviderRunPending(ConnectorError):
    """A billable provider run WAS submitted but its outcome is unknown (timeout/transport error).
    The run id is kept so it can be reconciled later; the run must never be submitted again."""

    def __init__(self, message: str, run_id: str) -> None:
        super().__init__(message)
        self.run_id = run_id
