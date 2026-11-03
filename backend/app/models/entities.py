import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin, JsonType, TimestampMixin, utcnow
from app.models.enums import (
    DealStage,
    OpportunityCategory,
    OpportunityStatus,
    OrganizationKind,
    OutreachStatus,
    RunStatus,
    VerificationStatus,
)


def _enum_check(column: str, enum_cls: type) -> str:
    values = ", ".join(f"'{m.value}'" for m in enum_cls)  # type: ignore[attr-defined]
    return f"{column} IN ({values})"


class WorkflowRun(Base, IdMixin, TimestampMixin):
    """Persistent job state. Doubles as the work queue for background workers."""

    __tablename__ = "workflow_runs"
    __table_args__ = (
        CheckConstraint(_enum_check("status", RunStatus), name="ck_workflow_runs_status"),
        Index("ix_workflow_runs_status_created", "status", "created_at"),
    )

    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=RunStatus.QUEUED, nullable=False)
    input: Mapped[dict] = mapped_column(JsonType, default=dict, nullable=False)  # type: ignore[type-arg]
    state: Mapped[dict] = mapped_column(JsonType, default=dict, nullable=False)  # type: ignore[type-arg]
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Evidence(Base, IdMixin):
    """Immutable-by-convention original source material behind every finding."""

    __tablename__ = "evidence"
    __table_args__ = (
        UniqueConstraint("content_hash", name="uq_evidence_content_hash"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_evidence_confidence"),
        CheckConstraint(
            _enum_check("verification_status", VerificationStatus),
            name="ck_evidence_verification_status",
        ),
    )

    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    verification_status: Mapped[str] = mapped_column(
        String(16), default=VerificationStatus.UNVERIFIED, nullable=False
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow_runs.id"))


class Organization(Base, IdMixin, TimestampMixin):
    __tablename__ = "organizations"
    __table_args__ = (
        CheckConstraint(_enum_check("kind", OrganizationKind), name="ck_organizations_kind"),
        UniqueConstraint("domain", name="uq_organizations_domain"),
    )

    name: Mapped[str] = mapped_column(String(256), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(253))
    kind: Mapped[str] = mapped_column(String(16), default=OrganizationKind.OTHER, nullable=False)
    country: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text)


class Contact(Base, IdMixin, TimestampMixin):
    """Only contacts that appear in evidence. Never fabricated or guessed."""

    __tablename__ = "contacts"
    __table_args__ = (
        CheckConstraint(
            _enum_check("verification_status", VerificationStatus),
            name="ck_contacts_verification_status",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    full_name: Mapped[str] = mapped_column(String(256), nullable=False)
    title: Mapped[str | None] = mapped_column(String(256))
    email: Mapped[str | None] = mapped_column(String(320))
    profile_url: Mapped[str | None] = mapped_column(String(2048))
    evidence_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evidence.id"), nullable=False)
    verification_status: Mapped[str] = mapped_column(
        String(16), default=VerificationStatus.UNVERIFIED, nullable=False
    )


class Opportunity(Base, IdMixin, TimestampMixin):
    __tablename__ = "opportunities"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_opportunities_dedupe_key"),
        CheckConstraint(_enum_check("category", OpportunityCategory), name="ck_opps_category"),
        CheckConstraint(_enum_check("status", OpportunityStatus), name="ck_opps_status"),
        CheckConstraint(
            _enum_check("verification_status", VerificationStatus), name="ck_opps_verification"
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_opps_confidence"),
        Index("ix_opportunities_category_status", "category", "status"),
    )

    category: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id"))
    status: Mapped[str] = mapped_column(String(16), default=OpportunityStatus.NEW, nullable=False)
    verification_status: Mapped[str] = mapped_column(
        String(16), default=VerificationStatus.UNVERIFIED, nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    evidence_links: Mapped[list["OpportunityEvidence"]] = relationship(
        back_populates="opportunity", cascade="all, delete-orphan"
    )


class OpportunityEvidence(Base):
    __tablename__ = "opportunity_evidence"

    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE"), primary_key=True
    )
    evidence_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evidence.id"), primary_key=True)

    opportunity: Mapped[Opportunity] = relationship(back_populates="evidence_links")


class Qualification(Base, IdMixin):
    __tablename__ = "qualifications"
    __table_args__ = (
        CheckConstraint("score >= 0 AND score <= 100", name="ck_qualifications_score"),
    )

    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False
    )
    score: Mapped[float] = mapped_column(Float, nullable=False)
    criteria: Mapped[dict] = mapped_column(JsonType, default=dict, nullable=False)  # type: ignore[type-arg]
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class OutreachDraft(Base, IdMixin, TimestampMixin):
    """Drafts only. Nothing is sent automatically; status must pass human approval."""

    __tablename__ = "outreach_drafts"
    __table_args__ = (
        CheckConstraint(_enum_check("status", OutreachStatus), name="ck_outreach_status"),
        CheckConstraint(
            "status NOT IN ('approved', 'sent') "
            "OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)",
            name="ck_outreach_requires_approval",
        ),
    )

    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contacts.id"))
    channel: Mapped[str] = mapped_column(String(32), default="email", nullable=False)
    subject: Mapped[str | None] = mapped_column(String(512))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), default=OutreachStatus.PENDING_APPROVAL, nullable=False
    )
    approved_by: Mapped[str | None] = mapped_column(String(256))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Deal(Base, IdMixin, TimestampMixin):
    __tablename__ = "deals"
    __table_args__ = (CheckConstraint(_enum_check("stage", DealStage), name="ck_deals_stage"),)

    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("opportunities.id"), nullable=False, unique=True
    )
    stage: Mapped[str] = mapped_column(String(16), default=DealStage.IDENTIFIED, nullable=False)
    value_usd: Mapped[float | None] = mapped_column(Numeric(14, 2))
    notes: Mapped[str | None] = mapped_column(Text)
    next_follow_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Interaction(Base, IdMixin):
    __tablename__ = "interactions"

    deal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("deals.id"), nullable=False)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contacts.id"))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class ProviderSpend(Base, IdMixin):
    """Ledger used to enforce per-run / daily / monthly provider spending limits."""

    __tablename__ = "provider_spend"
    __table_args__ = (
        CheckConstraint("amount_usd >= 0", name="ck_provider_spend_amount"),
        Index("ix_provider_spend_created", "created_at"),
    )

    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow_runs.id"))
    amount_usd: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    description: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
