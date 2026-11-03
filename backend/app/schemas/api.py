import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.connectors.base import DiscoveryQuery


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RunCreate(BaseModel):
    kind: str = "full_discovery"
    query: DiscoveryQuery = Field(default_factory=DiscoveryQuery)
    profile_id: uuid.UUID | None = None  # if set, the saved search profile defines the query


class ProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    keywords: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    connector_names: list[str] = Field(default_factory=list)
    max_results: int = Field(default=25, ge=1, le=500)
    max_age_days: int = Field(default=90, ge=1)


class ProfileUpdate(BaseModel):
    enabled: bool | None = None
    keywords: list[str] | None = None
    regions: list[str] | None = None
    connector_names: list[str] | None = None
    max_results: int | None = Field(default=None, ge=1, le=500)
    max_age_days: int | None = Field(default=None, ge=1)


class ProfileOut(ORM):
    id: uuid.UUID
    name: str
    keywords: list[str]
    regions: list[str]
    connector_names: list[str]
    max_results: int
    max_age_days: int
    enabled: bool


class RunOut(ORM):
    id: uuid.UUID
    kind: str
    status: str
    attempts: int
    error: str | None
    state: dict[str, Any]
    created_at: datetime
    finished_at: datetime | None


class EvidenceOut(ORM):
    id: uuid.UUID
    run_id: uuid.UUID | None = None
    provider: str
    source_url: str
    original_text: str
    published_at: datetime | None
    retrieved_at: datetime
    confidence: float
    verification_status: str
    source_check_status: str
    source_checked_at: datetime | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_mock(self) -> bool:
        return self.provider.startswith("mock")


class OpportunityOut(ORM):
    id: uuid.UUID
    category: str
    title: str
    status: str
    verification_status: str
    confidence: float
    organization_id: uuid.UUID | None
    first_seen_at: datetime
    last_seen_at: datetime


class OpportunityDetail(OpportunityOut):
    evidence: list[EvidenceOut]


class DraftOut(ORM):
    id: uuid.UUID
    opportunity_id: uuid.UUID
    contact_id: uuid.UUID | None
    channel: str
    subject: str | None
    body: str
    status: str
    approved_by: str | None
    approved_at: datetime | None


class ApprovalRequest(BaseModel):
    approver: str = Field(min_length=1, max_length=256)


class DraftUpdate(BaseModel):
    subject: str | None = None
    body: str = Field(min_length=1)


class AnalysisOut(ORM):
    id: uuid.UUID
    task: str
    provider: str
    model: str
    output: dict[str, Any] | None
    validated: bool
    rejection_reason: str | None
    created_at: datetime


class RouteOut(BaseModel):
    kind: str
    value: str
    role: str | None
    source_url: str
    snippet: str | None = None


class BoardRow(BaseModel):
    """One dashboard row: everything a reviewer needs, derived only from stored data."""

    id: uuid.UUID
    category: str
    title: str
    status: str
    verification_status: str
    confidence: float
    organization: str | None
    organization_kind: str | None
    country: str | None
    domain: str | None
    is_mock: bool
    score: float | None
    qualification_rationale: str | None
    evidence_count: int
    source_url: str | None
    published_at: datetime | None
    stated_budget: str | None  # None means unknown; never guessed
    ai_summary: str | None
    next_action: str
    last_seen_at: datetime
    triage: str
    technical_relevance: str
    rejection_reason: str | None
    source_status: str
    deadline: datetime | None
    contact_route: str | None
    missing: list[str]
    reasons: list[str]
    stage_a: dict[str, Any]
    stage_b: dict[str, Any]
    view: str  # which dashboard list this record belongs to
    evidence_excerpt: str | None
    channel: str
    project_type: str
    budget_value: float | None  # numeric only for EUR/USD/GBP; None = unknown, never guessed
    budget_currency: str | None
    age_days: int | None
    routes: list[RouteOut]
    ai_next_action: str | None
    small_team_fit: str | None
    external_delivery: str | None


class BudgetLine(BaseModel):
    family: str
    spent_usd: float
    cap_usd: float | None
    remaining_usd: float | None


class CostSummary(BaseModel):
    lines: list[BudgetLine]
    total_spent_usd: float
    total_cap_usd: float | None
    today_usd: float
    month_usd: float
    note: str
