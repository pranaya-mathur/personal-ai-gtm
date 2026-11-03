import enum


class OpportunityCategory(enum.StrEnum):
    """Strict separation of evidence classes. Never merge these buckets."""

    CONFIRMED_DEMAND = "confirmed_demand"  # a buyer publicly stated a project need
    VERIFIED_PARTNERSHIP = "verified_partnership"  # evidenced agency/partner activity
    SPECULATIVE_PROSPECT = "speculative_prospect"  # inferred fit only, no stated need


class VerificationStatus(enum.StrEnum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    REJECTED = "rejected"
    STALE = "stale"


class OrganizationKind(enum.StrEnum):
    BUYER = "buyer"
    AGENCY = "agency"
    OTHER = "other"


class OpportunityStatus(enum.StrEnum):
    NEW = "new"
    QUALIFIED = "qualified"
    DISQUALIFIED = "disqualified"
    ENGAGED = "engaged"
    WON = "won"
    LOST = "lost"


class RunStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class OutreachStatus(enum.StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    SENT = "sent"  # reserved; no sender exists in Milestone 1


class DealStage(enum.StrEnum):
    IDENTIFIED = "identified"
    CONTACTED = "contacted"
    CONVERSATION = "conversation"
    PROPOSAL = "proposal"
    NEGOTIATION = "negotiation"
    WON = "won"
    LOST = "lost"


class SourceCheckStatus(enum.StrEnum):
    NOT_CHECKED = "not_checked"
    CONFIRMED = "confirmed"  # cited page was fetched and contains the claim
    MISMATCH = "mismatch"  # page fetched but claim not found
    UNREACHABLE = "unreachable"
    BLOCKED = "blocked"  # robots.txt disallows, or URL failed safety checks
