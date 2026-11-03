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
    ConnectorDisabled,
    ConnectorError,
    ConnectorRejected,
    DiscoveryConnector,
    DiscoveryQuery,
    DiscoveryResult,
    ProviderRunPending,
    SignalKind,
)
from app.core.logging import get_logger
from app.services.ingest import ingest_finding, validate_finding
from app.services.provider_runs import RunLedger
from app.services.query_strategy import prefer_effective, record_search
from app.services.spend import Reservation, SpendGuard, SpendLimitExceeded

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
        self,
        session: Session,
        connectors: list[DiscoveryConnector],
        spend: SpendGuard,
        cache: dict[str, DiscoveryResult] | None = None,
    ) -> None:
        self.session = session
        self.spend = spend
        self.cache = cache if cache is not None else {}
        self.connectors = [c for c in connectors if c.kinds & self.kinds]

    def _feedback_in(self, connector: DiscoveryConnector) -> None:
        """Order a search connector's queries by what they measurably produced before."""
        if hasattr(connector, "set_plan") and hasattr(connector, "planned"):
            connector.query_log = {}  # type: ignore[attr-defined]
            connector.set_plan(prefer_effective(self.session, connector.planned()))  # type: ignore[attr-defined]

    def _feedback_out(self, connector: DiscoveryConnector, cost: float) -> None:
        """Persist what each executed query retrieved (outcomes are attributed after triage)."""
        from app.services.query_strategy import PlannedQuery, fingerprint

        log_ = getattr(connector, "query_log", None) or {}
        share = cost / len(log_) if log_ else 0.0
        for fp, e in log_.items():
            record_search(
                self.session,
                provider=connector.name,
                planned=PlannedQuery(e["channel"], e["text"], "executed", fp or fingerprint(e["text"])),
                urls=e["urls"],
                pages_ok=e["pages_ok"],
                pages_blocked=e["pages_blocked"],
                cost_usd=share,
            )

    def _call(
        self, connector: DiscoveryConnector, query: DiscoveryQuery, run_id: uuid.UUID | None
    ) -> DiscoveryResult:
        """Each attempt reserves its own worst-case cost before it can spend anything; a failed
        attempt keeps its reservation (a provider may bill a run that errored)."""
        if connector.name in self.cache:  # already fetched (and paid for) by the other agent
            return self.cache[connector.name]
        for attempt in Retrying(
            retry=retry_if_exception_type(ConnectorError)
            & retry_if_not_exception_type((ConnectorRejected, ProviderRunPending)),
            # a billable provider run is never re-submitted after a transport error: the first
            # run may still be executing (and billed) on the provider's side
            stop=stop_after_attempt(getattr(connector, "max_attempts", 3)),
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
                if reservation is not None:
                    ledger = RunLedger(
                        self.session,
                        connector.name,
                        run_id,
                        reservation.id,
                        lambda rid, r=reservation: self.spend.annotate(r, f"apify_run:{rid}"),
                    )
                    connector.ledger = ledger  # type: ignore[attr-defined]
                self._feedback_in(connector)
                try:
                    result = connector.discover(query)
                except (ConnectorRejected, ConnectorDisabled):
                    if reservation is not None:
                        self.spend.settle(
                            reservation, 0.0
                        )  # refused before any work: nothing billed
                    raise
                finally:
                    if reservation is not None:
                        connector.ledger = None  # type: ignore[attr-defined]
                if reservation is not None:
                    self.spend.settle(reservation, result.actual_cost_usd)
                self._feedback_out(connector, result.actual_cost_usd)
                self.cache[connector.name] = result
                return result
        raise AssertionError("unreachable")  # pragma: no cover

    def run(self, query: DiscoveryQuery, run_id: uuid.UUID | None) -> dict[str, object]:
        opportunity_ids: list[str] = []
        errors: list[str] = []
        created = stale = 0
        attribution: list[dict[str, object]] = []
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
            except ConnectorDisabled as exc:
                stats.append({**_stat(connector, status="skipped"), "note": str(exc)[:200]})
                log.info("connector_skipped", provider=connector.name, reason=str(exc)[:200])
                continue
            except ConnectorError as exc:
                errors.append(f"{connector.name}: {exc}")
                stats.append({**_stat(connector, errors=1, status="failed"), "error": str(exc)[:300]})
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
                if finding.query_fingerprint:
                    attribution.append(
                        {
                            "oid": str(out.opportunity_id),
                            "provider": connector.name,
                            "fp": finding.query_fingerprint,
                            "duplicate": bool(out.duplicate),
                        }
                    )
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
            "query_attribution": attribution,
        }


class OpportunityIntelligenceAgent(_DiscoveryAgent):
    agent_name = "opportunity_intelligence"
    kinds = frozenset({SignalKind.PROJECT_REQUEST, SignalKind.COMPANY_SIGNAL})


class AgencyPartnershipIntelligenceAgent(_DiscoveryAgent):
    agent_name = "agency_partnership_intelligence"
    kinds = frozenset({SignalKind.PARTNER_ACTIVITY})
