"""Controls added for the controlled live test: per-task models, test budgets, finding
validation, discovery job stats and cancellation."""

import pytest
from sqlalchemy import select

from app.ai import schemas as S
from app.ai.gateway import LLMGateway
from app.connectors.base import DiscoveryQuery, RawFinding, SignalKind
from app.connectors.registry import build_connectors
from app.core.config import Settings
from app.llm.base import StructuredResult
from app.models import ProviderSpend, WorkflowRun
from app.models.enums import RunStatus
from app.orchestrator.runner import claim_next_run, enqueue_run, execute_run
from app.services.ingest import validate_finding
from app.services.spend import SpendGuard, SpendLimitExceeded


def _settings(**kw) -> Settings:  # type: ignore[no-untyped-def]
    base = dict(database_url="sqlite://", openai_model="gpt-5.4-mini")
    return Settings(**{**base, **kw})  # type: ignore[arg-type]


def test_model_for_task_falls_back_to_default() -> None:
    s = _settings(openai_qualification_model="other-model")
    assert s.model_for("qualification") == "other-model"
    assert s.model_for("research_brief") == "gpt-5.4-mini"
    assert s.model_for("outreach_draft") == "gpt-5.4-mini"


def test_gateway_passes_task_model_to_client(session) -> None:  # type: ignore[no-untyped-def]
    seen: list[str | None] = []

    class Spy:
        provider, model, is_mock = "openai", "gpt-5.4-mini", False

        def ensure_ready(self) -> None: ...

        def complete_structured(self, system, user, schema, max_output_tokens, model=None):  # type: ignore[no-untyped-def]
            seen.append(model)
            return StructuredResult(
                parsed=S.OpportunityAnalysis(summary="x", claims=[]),
                provider="openai",
                model=model or "",
                input_tokens=10,
                output_tokens=5,
            )

    s = _settings(
        openai_research_model="research-model",
        openai_input_usd_per_mtok=0.75,
        openai_output_usd_per_mtok=4.5,
    )
    gw = LLMGateway(session, s, Spy(), SpendGuard(session, s))  # type: ignore[arg-type]
    import uuid

    gw.run_task(
        task="research_brief",
        schema=S.OpportunityAnalysis,
        system="s",
        user="u",
        run_id=None,
        opportunity_id=uuid.uuid4(),
        validator=lambda _o: None,
    )
    assert seen == ["research-model"]


def test_openai_test_budget_blocks_before_call(session) -> None:  # type: ignore[no-untyped-def]
    s = _settings(openai_test_budget_usd=0.50)
    guard = SpendGuard(session, s)
    guard.record("openai:gpt-5.4-mini", 0.49, None)
    guard.check(0.01, None, "openai")  # exactly at the cap is allowed
    with pytest.raises(SpendLimitExceeded, match="openai test budget"):
        guard.check(0.02, None, "openai")


def test_apify_cap_is_independent_and_total_cap_spans_providers(session) -> None:  # type: ignore[no-untyped-def]
    s = _settings(apify_test_budget_usd=0.25, total_test_budget_usd=0.75)
    guard = SpendGuard(session, s)
    guard.record("openai:gpt-5.4-mini", 0.50, None)
    guard.check(0.25, None, "apify")  # apify own cap fine, total 0.75 reached exactly
    guard.record("apify_ted_tenders", 0.20, None)
    with pytest.raises(SpendLimitExceeded, match="apify test budget"):
        guard.check(0.10, None, "apify")
    with pytest.raises(SpendLimitExceeded, match="total test budget"):
        guard.check(0.10, None, "openai")
    assert session.execute(select(ProviderSpend)).scalars().all()


def _finding(**kw) -> RawFinding:  # type: ignore[no-untyped-def]
    base = dict(
        kind=SignalKind.PROJECT_REQUEST,
        title="RAG platform",
        source_url="https://example.org/t/1",
        original_text="We seek a vendor.",
    )
    return RawFinding(**{**base, **kw})  # type: ignore[arg-type]


def test_validate_finding_rejects_untraceable_records() -> None:
    assert validate_finding(_finding()) is None
    assert validate_finding(_finding(title="  ")) == "missing title"
    assert validate_finding(_finding(original_text="")) == "missing source text"
    assert validate_finding(_finding(source_url="not a url")) is not None
    assert validate_finding(_finding(source_url="javascript:alert(1)")) is not None


def test_run_state_reports_connector_stats(session, settings) -> None:  # type: ignore[no-untyped-def]
    connectors = build_connectors(["mock_opportunities", "mock_agencies"])
    enqueue_run(session, "full_discovery", {"query": {}})
    run = claim_next_run(session)
    assert run is not None
    run = execute_run(session, run.id, connectors, settings)
    stats = run.state["connector_stats"]
    assert {st["connector"] for st in stats} == {"mock_opportunities", "mock_agencies"}
    assert all(st["is_mock"] for st in stats)
    assert sum(st["created"] for st in stats) == 5
    # second run: everything is a duplicate, nothing rejected
    enqueue_run(session, "full_discovery", {"query": {}})
    run2 = claim_next_run(session)
    assert run2 is not None
    run2 = execute_run(session, run2.id, connectors, settings)
    stats2 = run2.state["connector_stats"]
    assert sum(st["created"] for st in stats2) == 0
    assert sum(st["duplicates"] for st in stats2) == 5
    assert sum(st["rejected"] for st in stats2) == 0


def test_cancelled_run_stops_and_is_not_overwritten(session, settings) -> None:  # type: ignore[no-untyped-def]
    class Cancel:
        name, is_mock = "canceller", True
        kinds = frozenset({SignalKind.PROJECT_REQUEST})

        def estimate_cost(self, q):  # type: ignore[no-untyped-def]
            from app.connectors.base import CostEstimate

            return CostEstimate(usd=0)

        def discover(self, q: DiscoveryQuery):  # type: ignore[no-untyped-def]
            from app.connectors.base import DiscoveryResult

            run = session.execute(select(WorkflowRun)).scalars().one()
            run.status = RunStatus.CANCELLED  # user clicks cancel during the first node
            session.commit()
            return DiscoveryResult(provider="canceller", findings=[])

    enqueue_run(session, "full_discovery", {"query": {}})
    run = claim_next_run(session)
    assert run is not None
    run = execute_run(session, run.id, [Cancel()], settings)  # type: ignore[list-item]
    assert run.status == RunStatus.CANCELLED
    assert "pipeline_analytics" not in run.state.get("completed_nodes", [])


def test_run_timeout_is_terminal(session) -> None:  # type: ignore[no-untyped-def]
    s = _settings(run_timeout_seconds=0, worker_max_attempts=5)
    enqueue_run(session, "full_discovery", {"query": {}})
    run = claim_next_run(session)
    assert run is not None
    import time

    time.sleep(0.01)
    run = execute_run(session, run.id, build_connectors(["mock_opportunities"]), s)
    assert run.status == RunStatus.FAILED and "exceeded" in (run.error or "")


def test_truncated_openai_json_becomes_audited_refusal() -> None:
    import pytest
    from pydantic import SecretStr

    from app.llm.base import LLMRefusalError
    from app.llm.openai_client import OpenAILLM

    class Boom:
        class responses:  # noqa: N801
            @staticmethod
            def parse(**kw):  # type: ignore[no-untyped-def]
                S.ResearchBrief.model_validate_json('{"findings":[{"text":"Di')

    llm = OpenAILLM(SecretStr("k"), "gpt-5.4-mini", True, client=Boom())
    with pytest.raises(LLMRefusalError, match="not valid JSON"):
        llm.complete_structured("s", "u", S.ResearchBrief, 100)
