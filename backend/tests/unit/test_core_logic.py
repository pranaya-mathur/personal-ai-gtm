from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.agents.qualification import score_opportunity
from app.connectors.base import DiscoveryConnector, RawFinding, SignalKind
from app.connectors.mock import MockAgencyConnector, MockOpportunityConnector
from app.connectors.registry import build_connectors
from app.models import OutreachDraft
from app.models.enums import OpportunityCategory
from app.services.dedupe import evidence_hash, is_fresh, opportunity_dedupe_key
from app.services.ingest import classify
from app.services.spend import SpendGuard, SpendLimitExceeded


def _f(kind: SignalKind, text: str = "need", url: str = "https://x.example/a") -> RawFinding:
    return RawFinding(kind=kind, title="t", source_url=url, original_text=text)


def test_classification_keeps_buckets_separate() -> None:
    assert classify(_f(SignalKind.PROJECT_REQUEST)) == OpportunityCategory.CONFIRMED_DEMAND
    assert classify(_f(SignalKind.PARTNER_ACTIVITY)) == OpportunityCategory.VERIFIED_PARTNERSHIP
    assert classify(_f(SignalKind.COMPANY_SIGNAL)) == OpportunityCategory.SPECULATIVE_PROSPECT


def test_demand_without_original_text_is_not_promoted() -> None:
    assert (
        classify(_f(SignalKind.PROJECT_REQUEST, text="  "))
        == OpportunityCategory.SPECULATIVE_PROSPECT
    )
    assert (
        classify(_f(SignalKind.PROJECT_REQUEST, url="")) == OpportunityCategory.SPECULATIVE_PROSPECT
    )


def test_dedupe_keys_normalise() -> None:
    a = opportunity_dedupe_key("confirmed_demand", "Acme.com", "Build a  RAG bot!")
    b = opportunity_dedupe_key("confirmed_demand", "acme.com", "build a rag bot")
    assert a == b
    assert a != opportunity_dedupe_key("speculative_prospect", "acme.com", "build a rag bot")
    assert evidence_hash("p", "https://A/x", " t ") == evidence_hash("p", "https://a/x", "t")


def test_freshness() -> None:
    now = datetime.now(UTC)
    assert is_fresh(now - timedelta(days=10), 90)
    assert not is_fresh(now - timedelta(days=100), 90)
    assert not is_fresh(None, 90)  # undated is never assumed fresh
    assert is_fresh((now - timedelta(days=1)).replace(tzinfo=None), 90)


def test_registry_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError):
        build_connectors(["does_not_exist"])


def test_mock_connectors_conform_and_are_free() -> None:
    for c in (MockOpportunityConnector(), MockAgencyConnector()):
        assert isinstance(c, DiscoveryConnector)
        assert c.is_mock
        from app.connectors.base import DiscoveryQuery

        q = DiscoveryQuery()
        assert c.estimate_cost(q).usd == 0
        assert all(f.kind in c.kinds for f in c.discover(q).findings)


def test_spend_guard_blocks_before_call(session, settings) -> None:  # type: ignore[no-untyped-def]
    guard = SpendGuard(session, settings)
    guard.check(0.5, None)
    with pytest.raises(SpendLimitExceeded):
        guard.check(6.0, None)
    guard.record("p", 4.9, None)
    with pytest.raises(SpendLimitExceeded):
        guard.check(0.2, None)  # daily 5.0


def test_score_rubric() -> None:
    research = {
        "all_fresh": True,
        "has_publication_date": True,
        "has_source_url": True,
        "known_contacts": 1,
    }
    strong, _ = score_opportunity("confirmed_demand", 0.8, research)
    weak, _ = score_opportunity("speculative_prospect", 0.3, {"all_fresh": False})
    assert strong > 60 > weak


def test_unapproved_draft_cannot_be_marked_approved(session) -> None:  # type: ignore[no-untyped-def]
    import uuid

    from app.models import Opportunity

    opp = Opportunity(category="confirmed_demand", title="x", confidence=0.5, dedupe_key="k")
    session.add(opp)
    session.flush()
    session.add(OutreachDraft(opportunity_id=opp.id, body="b", status="approved"))
    with pytest.raises(IntegrityError):
        session.flush()
    _ = uuid
