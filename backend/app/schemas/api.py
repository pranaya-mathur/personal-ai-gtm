import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

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
