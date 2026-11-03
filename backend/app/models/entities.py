import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
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
    SourceCheckStatus,
    SourceStatus,
    TechnicalRelevance,
    Triage,
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
        CheckConstraint(
            _enum_check("source_check_status", SourceCheckStatus),
            name="ck_evidence_source_check_status",
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
    # Did the cited page actually contain the claim? Distinct from human verification.
    source_check_status: Mapped[str] = mapped_column(
        String(24), default=SourceCheckStatus.NOT_CHECKED, nullable=False
    )
    source_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
        CheckConstraint(_enum_check("triage", Triage), name="ck_opps_triage"),
        CheckConstraint(
            _enum_check("technical_relevance", TechnicalRelevance), name="ck_opps_tech_relevance"
        ),
        CheckConstraint(_enum_check("source_status", SourceStatus), name="ck_opps_source_status"),
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
    # Structured facts taken from the provider record (None/unknown = not stated by the source).
    source_status: Mapped[str] = mapped_column(
        String(16), default=SourceStatus.UNKNOWN, server_default="unknown", nullable=False
    )
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    budget_text: Mapped[str | None] = mapped_column(String(256))
    contact_route: Mapped[str | None] = mapped_column(String(2048))
    # Two-stage qualification (see services/triage.py); reasons and gaps are stored, not inferred.
    technical_relevance: Mapped[str] = mapped_column(
        String(16),
        default=TechnicalRelevance.UNASSESSED,
        server_default="unassessed",
        nullable=False,
    )
    triage: Mapped[str] = mapped_column(
        String(24), default=Triage.UNASSESSED, server_default="unassessed", nullable=False
    )
    rejection_reason: Mapped[str | None] = mapped_column(String(64))
    channel: Mapped[str] = mapped_column(
        String(16), default="direct_buyer", server_default="direct_buyer", nullable=False
    )  # agency | direct_buyer
    verified_by: Mapped[str | None] = mapped_column(String(128))  # authenticated identity
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assessment: Mapped[dict] = mapped_column(JsonType, default=dict, nullable=False)  # type: ignore[type-arg]
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


class SearchProfile(Base, IdMixin, TimestampMixin):
    """A saved, configurable worldwide search: terms, regions and providers."""

    __tablename__ = "search_profiles"
    __table_args__ = (
        UniqueConstraint("name", name="uq_search_profiles_name"),
        CheckConstraint("max_results >= 1 AND max_results <= 500", name="ck_profiles_max_results"),
    )

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    keywords: Mapped[list] = mapped_column(JsonType, default=list, nullable=False)  # type: ignore[type-arg]
    regions: Mapped[list] = mapped_column(JsonType, default=list, nullable=False)  # type: ignore[type-arg]
    connector_names: Mapped[list] = mapped_column(JsonType, default=list, nullable=False)  # type: ignore[type-arg]
    max_results: Mapped[int] = mapped_column(Integer, default=25, nullable=False)
    max_age_days: Mapped[int] = mapped_column(Integer, default=90, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class LlmUsage(Base, IdMixin):
    """Token + cost ledger for every LLM attempt (accepted or rejected)."""

    __tablename__ = "llm_usage"
    __table_args__ = (Index("ix_llm_usage_run", "run_id"),)

    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow_runs.id"))
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("opportunities.id"))
    task: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Numeric(14, 8), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)  # accepted | rejected | cached
    agent: Mapped[str | None] = mapped_column(String(48))
    reason: Mapped[str | None] = mapped_column(Text)  # why an output was rejected
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class AiAnalysis(Base, IdMixin):
    """Persisted AI output with its validation verdict. Rejected outputs are kept for audit."""

    __tablename__ = "ai_analyses"
    __table_args__ = (
        Index("ix_ai_analyses_opportunity", "opportunity_id"),
        Index("ix_ai_analyses_input_hash", "input_hash"),
    )

    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow_runs.id"))
    task: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    output: Mapped[dict | None] = mapped_column(JsonType)  # type: ignore[type-arg]
    validated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    input_hash: Mapped[str | None] = mapped_column(String(64))  # cache key: task+model+prompt
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class SourceCandidate(Base, IdMixin, TimestampMixin):
    """An organization/page found by automatic SOURCE DISCOVERY. It is only a lead to read: no
    commercial claim exists until its pages are fetched and verified as evidence."""

    __tablename__ = "source_candidates"
    __table_args__ = (
        UniqueConstraint("domain", name="uq_source_candidates_domain"),
        CheckConstraint(
            "status IN ('new', 'signal', 'no_signal', 'blocked', 'error')",
            name="ck_source_candidates_status",
        ),
    )

    domain: Mapped[str] = mapped_column(String(253), nullable=False)
    name: Mapped[str | None] = mapped_column(String(256))
    country: Mapped[str | None] = mapped_column(String(64))
    origin: Mapped[str] = mapped_column(String(48), nullable=False)  # wikidata | github | search
    origin_ref: Mapped[str | None] = mapped_column(String(2048))  # where it was discovered
    page_url: Mapped[str | None] = mapped_column(String(2048))  # specific page to read first
    status: Mapped[str] = mapped_column(String(16), default="new", nullable=False)
    status_detail: Mapped[str | None] = mapped_column(String(256))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    signals: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class DiscoveryQueryStat(Base, IdMixin, TimestampMixin):
    """Effectiveness ledger for intent/search queries. Never promotes a query into evidence."""

    __tablename__ = "discovery_query_stats"
    __table_args__ = (
        UniqueConstraint("fingerprint", "provider", name="uq_discovery_query_stats_fp_provider"),
        Index("ix_discovery_query_stats_channel", "channel"),
    )

    fingerprint: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)  # agency | buyer
    query_text: Mapped[str] = mapped_column(String(512), nullable=False)
    searches: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    urls_retrieved: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pages_ok: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pages_blocked: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    confirmed_active: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    agency_partner_need: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    needs_verification: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    false_positives: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicates: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 4), default=0, nullable=False)


class ContactRoute(Base, IdMixin, TimestampMixin):
    """A published way to reach a buyer/partner, exactly as the source prints it."""

    __tablename__ = "contact_routes"
    __table_args__ = (
        UniqueConstraint("opportunity_id", "kind", "value", name="uq_contact_routes_value"),
        CheckConstraint(
            "kind IN ('email', 'inquiry_form', 'procurement_link', 'page')",
            name="ck_contact_routes_kind",
        ),
        Index("ix_contact_routes_org", "organization_id"),
    )

    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id"))
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    value: Mapped[str] = mapped_column(String(2048), nullable=False)
    role: Mapped[str | None] = mapped_column(String(256))  # only if the source states a role
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    snippet: Mapped[str | None] = mapped_column(Text)  # source text the route came from


class CapabilityProfile(Base, IdMixin, TimestampMixin):
    """The owner's approved capabilities. One row. Areas are what the owner CLAIMS; a project
    reference counts only when the owner adds it as verified. Nothing is inferred or invented."""

    __tablename__ = "capability_profile"

    areas: Mapped[list] = mapped_column(JsonType, default=list, nullable=False)  # type: ignore[type-arg]
    updated_by: Mapped[str | None] = mapped_column(String(128))


class Proposal(Base, IdMixin, TimestampMixin):
    """A proposal draft. Human-approved before it is marked sent; commitments stay human."""

    __tablename__ = "proposals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'approved', 'sent', 'accepted', 'declined')",
            name="ck_proposals_status",
        ),
        CheckConstraint(
            "status NOT IN ('approved', 'sent', 'accepted') "
            "OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)",
            name="ck_proposals_requires_approval",
        ),
    )

    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    scope: Mapped[dict] = mapped_column(JsonType, default=dict, nullable=False)  # type: ignore[type-arg]
    body: Mapped[str] = mapped_column(Text, nullable=False)
    price_text: Mapped[str | None] = mapped_column(String(256))  # None = not set by the owner
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)
    prepared_by: Mapped[str] = mapped_column(String(128), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(128))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OnboardingItem(Base, IdMixin, TimestampMixin):
    __tablename__ = "onboarding_items"
    __table_args__ = (UniqueConstraint("deal_id", "label", name="uq_onboarding_label"),)

    deal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("deals.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(300), nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    done: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    done_by: Mapped[str | None] = mapped_column(String(128))
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
