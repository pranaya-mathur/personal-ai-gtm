"""Offline fixture providers. No network, zero cost. Data is fictional (.example domains)."""

from datetime import UTC, datetime, timedelta

from app.connectors.base import (
    CostEstimate,
    DiscoveryQuery,
    DiscoveryResult,
    RawContact,
    RawFinding,
    SignalKind,
)

_TAG = "[MOCK FIXTURE] "


def _days_ago(n: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=n)


class MockOpportunityConnector:
    name = "mock_opportunities"
    kinds = frozenset({SignalKind.PROJECT_REQUEST, SignalKind.COMPANY_SIGNAL})
    is_mock = True

    def estimate_cost(self, query: DiscoveryQuery) -> CostEstimate:
        return CostEstimate(usd=0.0, note="mock provider")

    def discover(self, query: DiscoveryQuery) -> DiscoveryResult:
        findings = [
            RawFinding(
                kind=SignalKind.PROJECT_REQUEST,
                title="RAG assistant for internal support knowledge base",
                source_url="https://mock-buyer-one.example/posts/rag-assistant",
                original_text=_TAG + "We are looking for a team to build a retrieval-augmented "
                "assistant over our support documentation.",
                published_at=_days_ago(5),
                organization_name="Mock Buyer One",
                organization_domain="mock-buyer-one.example",
                organization_country="DE",
                provider_confidence=0.8,
                source_status="open",
                deadline=_days_ago(-30),
                contact_route="https://mock-buyer-one.example/contact",
            ),
            RawFinding(
                kind=SignalKind.PROJECT_REQUEST,
                title="Document-processing automation pilot",
                source_url="https://mock-buyer-two.example/posts/doc-automation",
                original_text=_TAG + "Seeking a vendor for an LLM document-processing pilot.",
                published_at=_days_ago(400),  # deliberately stale
                organization_name="Mock Buyer Two",
                organization_domain="mock-buyer-two.example",
                provider_confidence=0.7,
                source_status="closed",
            ),
            RawFinding(
                kind=SignalKind.COMPANY_SIGNAL,
                title="Mock Buyer Three expanded its data team",
                source_url="https://mock-buyer-three.example/news/data-team",
                original_text=_TAG + "Announced hiring of five data engineers.",
                published_at=_days_ago(12),
                organization_name="Mock Buyer Three",
                organization_domain="mock-buyer-three.example",
                provider_confidence=0.3,
            ),
        ]
        return DiscoveryResult(
            provider=self.name, findings=findings[: query.max_results], actual_cost_usd=0.0
        )


class MockAgencyConnector:
    name = "mock_agencies"
    kinds = frozenset({SignalKind.PARTNER_ACTIVITY})
    is_mock = True

    def estimate_cost(self, query: DiscoveryQuery) -> CostEstimate:
        return CostEstimate(usd=0.0, note="mock provider")

    def discover(self, query: DiscoveryQuery) -> DiscoveryResult:
        findings = [
            RawFinding(
                kind=SignalKind.PARTNER_ACTIVITY,
                title="Mock Agency Alpha publishes white-label AI delivery partner program",
                source_url="https://mock-agency-alpha.example/partners",
                original_text=_TAG + "Our partner program offers white-label AI engineering "
                "capacity to other agencies.",
                published_at=_days_ago(20),
                organization_name="Mock Agency Alpha",
                organization_domain="mock-agency-alpha.example",
                organization_country="GB",
                contacts=[
                    RawContact(
                        full_name="Pat Example",
                        title="Head of Partnerships",
                        profile_url="https://mock-agency-alpha.example/team/pat",
                    )
                ],
                provider_confidence=0.75,
            ),
            RawFinding(
                kind=SignalKind.PARTNER_ACTIVITY,
                title="Mock Agency Beta case study: subcontracted ML team",
                source_url="https://mock-agency-beta.example/case-studies/subcontract",
                original_text=_TAG + "Delivered with a subcontracted machine-learning team.",
                published_at=_days_ago(45),
                organization_name="Mock Agency Beta",
                organization_domain="mock-agency-beta.example",
                provider_confidence=0.6,
            ),
        ]
        return DiscoveryResult(
            provider=self.name, findings=findings[: query.max_results], actual_cost_usd=0.0
        )
