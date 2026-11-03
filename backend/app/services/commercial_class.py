"""Map stored category + triage + channel onto the six commercial-intent classes.

Deterministic. Never invents demand: speculative and partnership-model records stay distinct
from confirmed active requests.
"""

from app.models.enums import OpportunityCategory, Triage

# Stable identifiers used in API/dashboard and query-effectiveness rollups.
CONFIRMED_ACTIVE_REQUEST = "confirmed_active_ai_request"
ACTIVE_AGENCY_PARTNER_NEED = "active_agency_delivery_partner_need"
VERIFIED_AGENCY_MODEL = "verified_agency_partnership_model"
POTENTIAL_NO_DEMAND = "potential_without_current_demand"
NEEDS_VERIFICATION = "ai_related_needs_verification"
REJECTED = "rejected_expired_or_irrelevant"

LABELS = {
    CONFIRMED_ACTIVE_REQUEST: "Confirmed active AI implementation request",
    ACTIVE_AGENCY_PARTNER_NEED: "Active agency delivery-partner requirement",
    VERIFIED_AGENCY_MODEL: "Verified agency partnership/delivery model",
    POTENTIAL_NO_DEMAND: "Potential buyer or partner without current demand evidence",
    NEEDS_VERIFICATION: "AI-related requirement needing verification",
    REJECTED: "Rejected, expired or irrelevant",
}


def commercial_class(*, category: str, triage: str, channel: str) -> str:
    if triage == Triage.REJECTED:
        return REJECTED
    if category == OpportunityCategory.SPECULATIVE_PROSPECT:
        return POTENTIAL_NO_DEMAND
    if category == OpportunityCategory.VERIFIED_PARTNERSHIP:
        return VERIFIED_AGENCY_MODEL
    agency = channel == "agency"
    if triage == Triage.ACTIONABLE:
        return ACTIVE_AGENCY_PARTNER_NEED if agency else CONFIRMED_ACTIVE_REQUEST
    if triage == Triage.NEEDS_VERIFICATION:
        if agency and category == OpportunityCategory.CONFIRMED_DEMAND:
            return ACTIVE_AGENCY_PARTNER_NEED  # stated need; human must confirm currency
        return NEEDS_VERIFICATION
    return NEEDS_VERIFICATION
