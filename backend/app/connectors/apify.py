from app.connectors.apify_client import ApifyClient
from app.connectors.apify_specs import ActorSpec
from app.connectors.base import (
    ConnectorDisabled,
    ConnectorError,
    CostEstimate,
    DiscoveryQuery,
    DiscoveryResult,
    RawFinding,
)
from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger(__name__)


class ApifyActorConnector:
    """Generic connector driven by an ActorSpec. Paid execution needs three explicit opt-ins:
    a spec marked reviewed, APIFY_TOKEN, and APIFY_PAID_EXECUTION_APPROVED=true."""

    is_mock = False
    max_attempts = 1  # billable runs are not retried

    def __init__(
        self, spec: ActorSpec, settings: Settings, client: ApifyClient | None = None
    ) -> None:
        if not spec.reviewed:
            raise ValueError(f"{spec.connector_name} is not approved for use: {spec.notes}")
        self.spec = spec
        self.settings = settings
        self._client = client
        self.name = spec.connector_name
        self.kinds = frozenset({spec.kind})

    def _results_cap(self, query: DiscoveryQuery) -> int:
        return min(query.max_results, self.spec.max_results_cap)

    def estimate_cost(self, query: DiscoveryQuery) -> CostEstimate:
        usd = self.spec.estimate_usd(self._results_cap(query))
        return CostEstimate(usd=usd, note=f"upper bound for {self._results_cap(query)} results")

    def _get_client(self) -> ApifyClient:
        if self._client is not None:
            return self._client
        token = self.settings.apify_token
        if token is None or not token.get_secret_value():
            raise ConnectorError("APIFY_TOKEN is not set")
        return ApifyClient(token, self.settings.apify_api_base)

    def discover(self, query: DiscoveryQuery) -> DiscoveryResult:
        if not self.settings.apify_paid_execution_approved:
            raise ConnectorDisabled(
                "Paid Apify execution is not approved (APIFY_PAID_EXECUTION_APPROVED=false)"
            )
        cap = self._results_cap(query)
        max_charge = min(self.estimate_cost(query).usd, self.settings.spend_limit_per_run_usd)
        client = self._get_client()
        run = client.run_actor(
            self.spec.actor_id,
            self.spec.build_input(query, cap),
            max_charge,
            getattr(self, "ledger", None),
        )
        items = client.dataset_items(run["defaultDatasetId"], cap)
        findings: list[RawFinding] = []
        skipped = 0
        for item in items:
            finding = self.spec.map_item(item, query)
            if finding is None:
                skipped += 1
                continue
            findings.append(finding)
        log.info(
            "apify_discovery_done",
            provider=self.name,
            items=len(items),
            mapped=len(findings),
            skipped=skipped,
        )
        reported = run.get("usageTotalUsd")
        # Pay-per-result charges are not yet in usageTotalUsd when the run ends (the first TED run
        # showed $0.00005 but billed $0.076). Never under-record: take the larger of the provider
        # figure and the published price applied to the items actually returned.
        expected = self.spec.estimate_usd(len(items))
        actual = max(float(reported) if isinstance(reported, int | float) else 0.0, expected)
        return DiscoveryResult(
            provider=self.name, findings=findings, actual_cost_usd=min(actual, max_charge)
        )
