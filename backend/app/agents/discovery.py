import uuid

from sqlalchemy.orm import Session
from tenacity import (
    Retrying,
    retry_if_exception_type,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.connectors.base import (
    ConnectorError,
    ConnectorRejected,
    DiscoveryConnector,
    DiscoveryQuery,
    DiscoveryResult,
    SignalKind,
)
from app.core.logging import get_logger
from app.services.ingest import ingest_finding, validate_finding
from app.services.spend import SpendGuard, SpendLimitExceeded

log = get_logger(__name__)


def _stat(
    connector: DiscoveryConnector, errors: int = 0, status: str = "", cost: float = 0.0
) -> dict[str, object]:
    return {
        "connector": connector.name,
        "is_mock": connector.is_mock,
        "status": status,
        "retrieved": 0,
        "rejected": 0,
        "created": 0,
        "duplicates": 0,
        "errors": errors,
        "cost_usd": cost,
    }


class _DiscoveryAgent:
    kinds: frozenset[SignalKind]
    agent_name: str

    def __init__(
        self, session: Session, connectors: list[DiscoveryConnector], spend: SpendGuard
    ) -> None:
        self.session = session
        self.spend = spend
        self.connectors = [c for c in connectors if c.kinds & self.kinds]

    def _call(
        self, connector: DiscoveryConnector, query: DiscoveryQuery, run_id: uuid.UUID | None
    ) -> DiscoveryResult:
        """Each attempt reserves its own worst-case cost before it can spend anything; a failed
        attempt keeps its reservation (a provider may bill a run that errored)."""
        for attempt in Retrying(
            retry=retry_if_exception_type(ConnectorError)
            & retry_if_not_exception_type(ConnectorRejected),
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=0.5, max=8),
            reraise=True,
        ):
            with attempt:
                est = connector.estimate_cost(query).usd
                family = getattr(connector, "spend_family", None) or (
                    "apify" if connector.name.startswith("apify") else None
                )
                reservation = None
                if est > 0:
                    ledger_name = (
                        f"{family}:{connector.name}"
                        if getattr(connector, "spend_family", None)
                        else connector.name
                    )
                    reservation = self.spend.reserve(est, run_id, family, ledger_name, "discovery")
                else:
                    self.spend.check(0.0, run_id, family)
                try:
                    result = connector.discover(query)
                except ConnectorRejected:
                    if reservation is not None:
                        self.spend.settle(
                            reservation, 0.0
                        )  # refused before any work: nothing billed
                    raise
                if reservation is not None:
                    self.spend.settle(reservation, result.actual_cost_usd)
                return result
        raise AssertionError("unreachable")  # pragma: no cover

    def run(self, query: DiscoveryQuery, run_id: uuid.UUID | None) -> dict[str, object]:
        opportunity_ids: list[str] = []
        errors: list[str] = []
        created = stale = 0
        stats: list[dict[str, object]] = []
        for connector in self.connectors:
            if query.connector_names is not None and connector.name not in query.connector_names:
                continue
            try:
                result = self._call(connector, query, run_id)
            except SpendLimitExceeded as exc:
                errors.append(f"{connector.name}: blocked ({exc})")
                stats.append(_stat(connector, errors=1, status="blocked"))
                log.warning("spend_blocked", provider=connector.name, reason=str(exc))
                continue
            except ConnectorError as exc:
                errors.append(f"{connector.name}: {exc}")
                stats.append(_stat(connector, errors=1, status="failed"))
                log.error("connector_failed", provider=connector.name, error=str(exc))
                continue
            st = _stat(connector, status="succeeded", cost=result.actual_cost_usd)
            stats.append(st)
            for finding in result.findings:
                if finding.kind not in self.kinds:
                    continue  # a provider must not leak kinds into the wrong agent's bucket
                st["retrieved"] = int(st["retrieved"]) + 1  # type: ignore[call-overload]
                reason = validate_finding(finding)
                if reason is not None:
                    st["rejected"] = int(st["rejected"]) + 1  # type: ignore[call-overload]
                    log.warning("finding_rejected", provider=connector.name, reason=reason)
                    continue
                out = ingest_finding(
                    self.session, finding, connector.name, run_id, query.max_age_days
                )
                opportunity_ids.append(str(out.opportunity_id))
                created += out.created
                st["created"] = int(st["created"]) + int(out.created)  # type: ignore[call-overload]
                st["duplicates"] = int(st["duplicates"]) + int(out.duplicate)  # type: ignore[call-overload]
                stale += out.stale
        return {
            "agent": self.agent_name,
            "opportunity_ids": sorted(set(opportunity_ids)),
            "created": created,
            "stale": stale,
            "errors": errors,
            "connector_stats": stats,
        }


class OpportunityIntelligenceAgent(_DiscoveryAgent):
    agent_name = "opportunity_intelligence"
    kinds = frozenset({SignalKind.PROJECT_REQUEST, SignalKind.COMPANY_SIGNAL})


class AgencyPartnershipIntelligenceAgent(_DiscoveryAgent):
    agent_name = "agency_partnership_intelligence"
    kinds = frozenset({SignalKind.PARTNER_ACTIVITY})
