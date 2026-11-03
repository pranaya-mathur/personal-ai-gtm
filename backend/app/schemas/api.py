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
    provider: str
    source_url: str
    original_text: str
    published_at: datetime | None
    retrieved_at: datetime
    confidence: float
    verification_status: str


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
