import math
import uuid
from typing import Any

import httpx
import openai
import pytest
from sqlalchemy import func, select

from app.ai import schemas as S
from app.ai.claims import ClaimValidationError, norm, validate_output
from app.ai.gateway import LLMGateway
from app.connectors.registry import build_connectors
from app.core.config import Settings
from app.llm import (
    LLMDisabledError,
    LLMRefusalError,
    LLMTransientError,
    StructuredResult,
    build_llm,
)
from app.llm.mock import MockLLM
from app.llm.openai_client import OpenAILLM
from app.models import (
    AiAnalysis,
    Evidence,
    LlmUsage,
    Opportunity,
    OutreachDraft,
    ProviderSpend,
    Qualification,
)
from app.orchestrator.runner import claim_next_run, enqueue_run, execute_run
from app.services.spend import SpendGuard, SpendLimitExceeded

EV = {"e1": "We are looking for a team to build a retrieval-augmented assistant. Budget: $50,000."}
URLS = {"https://buyer.example/post"}


def claim(quote: str = "build a retrieval-augmented assistant", eid: str = "e1") -> S.Claim:
    return S.Claim(text="Buyer wants a RAG assistant", evidence_id=eid, quote=quote)


# ------------------------------------------------------------------ claim guard
def test_grounded_claim_passes_even_with_case_and_punctuation_noise() -> None:
    validate_output(
        S.OpportunityAnalysis(
            summary="s", claims=[claim("BUILD a retrieval-augmented assistant!")]
        ),
        EV,
        min_claims=1,
    )


@pytest.mark.parametrize(
    ("claims", "needle"),
    [
        ([claim("build a fraud detection system")], "not found verbatim"),
        ([claim(eid="nope")], "unknown evidence_id"),
        ([claim("team")], "too short"),
        ([], "at least 1"),
    ],
)
def test_ungrounded_claims_rejected(claims: list[S.Claim], needle: str) -> None:
    with pytest.raises(ClaimValidationError, match=needle):
        validate_output(S.OpportunityAnalysis(summary="s", claims=claims), EV, min_claims=1)


def test_nested_claims_are_checked() -> None:
    bad = S.QualificationAssessment(
        fit_score=80,
        criteria=[
            S.CriterionAssessment(
                name="n", score=50, rationale="r", support=claim("invented quote text")
            )
        ],
        risks=[],
    )
    with pytest.raises(ClaimValidationError, match="verbatim"):
        validate_output(bad, EV)
    with pytest.raises(ClaimValidationError, match="fit_score"):
        validate_output(S.QualificationAssessment(fit_score=140, criteria=[], risks=[]), EV)
    with pytest.raises(ClaimValidationError, match="reliability"):
        validate_output(
            S.EvidenceEvaluation(verdict="supported", reliability=1.5, issues=[], supporting=[]), EV
        )


def test_outreach_cannot_invent_urls_or_amounts() -> None:
    def draft(body: str) -> S.OutreachDraftAI:
        return S.OutreachDraftAI(subject="s", body=body, claims=[claim()])

    validate_output(draft("Saw your post https://buyer.example/post about $50,000."), EV, 1, URLS)
    with pytest.raises(ClaimValidationError, match="URL"):
        validate_output(draft("See https://evil.example/x"), EV, 1, URLS)
    with pytest.raises(ClaimValidationError, match="amount"):
        validate_output(draft("We can do it for $20,000."), EV, 1, URLS)


def test_ai_schemas_are_strict_structured_output_compatible() -> None:
    def walk(node: Any, defs: dict[str, Any]) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" or "properties" in node:
                assert node.get("additionalProperties") is False
                assert set(node["properties"]) == set(node.get("required", []))
            for v in node.values():
                walk(v, defs)
        elif isinstance(node, list):
            for v in node:
                walk(v, defs)

    for model in (
        S.OpportunityAnalysis, S.RequirementExtraction, S.EvidenceEvaluation,
        S.ResearchBrief, S.QualificationAssessment, S.OutreachDraftAI,
    ):  # fmt: skip
        schema = model.model_json_schema()
        walk(schema, schema.get("$defs", {}))


def test_norm_is_stable() -> None:
    assert norm("A,  b!") == norm("a b")


# ------------------------------------------------------------------ test doubles
class RoutingLLM:
    """Delegates to MockLLM (valid grounded output) with per-schema overrides and token counts."""

    provider, model = "openai", "test-model"

    def __init__(self, is_mock: bool = False, tokens: tuple[int, int] = (1000, 500)) -> None:
        self.is_mock, self.tokens = is_mock, tokens
        self.calls: list[tuple[type, str]] = []
        self.handlers: dict[type, Any] = {}

    def ensure_ready(self) -> None:
        return None

    def complete_structured(self, system, user, schema, max_output_tokens, model=None):  # type: ignore[no-untyped-def]
        self.calls.append((schema, user))
        if schema in self.handlers:
            out = self.handlers[schema](len(self.calls), user)
            if isinstance(out, Exception):
                raise out
        else:
            out = MockLLM().complete_structured(system, user, schema, max_output_tokens).parsed
        return StructuredResult(
            parsed=out, provider=self.provider, model=self.model,
            input_tokens=self.tokens[0], output_tokens=self.tokens[1],
        )  # fmt: skip


def live(**kw: Any) -> Settings:
    base: dict[str, Any] = dict(
        database_url="sqlite://", llm_mock_mode=False, llm_paid_calls_enabled=True,
        openai_api_key="k", openai_model="test-model", openai_input_usd_per_mtok=1.0,
        openai_output_usd_per_mtok=2.0, llm_retry_base_seconds=0.0,
    )  # fmt: skip
    base.update(kw)
    return Settings(**base)


def seed(session, n: int = 1):  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime

    from app.connectors.base import RawFinding, SignalKind
    from app.services.ingest import ingest_finding

    ids = []
    for i in range(n):
        f = RawFinding(
            kind=SignalKind.PROJECT_REQUEST, title=f"RAG assistant {i} for support docs",
            source_url=f"https://buyer.example/post/{i}",
            original_text="We are looking for a team to build a retrieval-augmented assistant.",
            published_at=datetime.now(UTC), organization_name=f"Buyer {i}",
            organization_domain=f"buyer{i}.example", provider_confidence=0.8,
        )  # fmt: skip
        ids.append(ingest_finding(session, f, "t", None, 90).opportunity_id)
    session.commit()
    return ids


def gateway(session, settings, llm):  # type: ignore[no-untyped-def]
    return LLMGateway(session, settings, llm, SpendGuard(session, settings))


def evidence_block(session):  # type: ignore[no-untyped-def]
    rows = session.execute(select(Evidence)).scalars().all()
    ev = {str(e.id): e.original_text for e in rows}
    user = "\n".join(
        f'<evidence id="{e.id}" url="{e.source_url}">\n{e.original_text}\n</evidence>' for e in rows
    )
    return ev, user


def run_task(gw, session, opp_id, validator=None, run_id=None):  # type: ignore[no-untyped-def]
    ev, user = evidence_block(session)
    return gw.run_task(
        task="opportunity_analysis", schema=S.OpportunityAnalysis, system="sys",
        user=user, run_id=run_id, opportunity_id=opp_id,
        validator=validator or (lambda o: validate_output(o, ev, 1)),
    )  # fmt: skip


# ------------------------------------------------------------------ gateway
def test_gateway_accounts_tokens_and_cost(session) -> None:  # type: ignore[no-untyped-def]
    opp = seed(session)[0]
    s = live()
    gw = gateway(session, s, RoutingLLM())
    assert run_task(gw, session, opp) is not None
    row = session.execute(select(LlmUsage)).scalar_one()
    assert (row.input_tokens, row.output_tokens, row.outcome) == (1000, 500, "accepted")
    assert float(row.cost_usd) == pytest.approx(0.002)  # 1000*1 + 500*2 per 1M tokens
    spend = session.execute(select(ProviderSpend)).scalar_one()
    assert spend.provider == "openai:test-model" and float(spend.amount_usd) == pytest.approx(0.002)
    assert session.execute(select(AiAnalysis)).scalar_one().validated is True


def test_gateway_requires_prices_for_live_calls(session) -> None:  # type: ignore[no-untyped-def]
    ok, reason = gateway(session, live(openai_input_usd_per_mtok=None), RoutingLLM()).availability()
    assert not ok and "OPENAI_INPUT_USD_PER_MTOK" in reason
    assert gateway(session, Settings(), MockLLM()).availability() == (True, "ready")


def test_rejected_output_gets_one_metered_repair_attempt(session) -> None:  # type: ignore[no-untyped-def]
    opp = seed(session)[0]
    llm = RoutingLLM()

    def handler(n: int, user: str):  # type: ignore[no-untyped-def]
        if n == 1:
            return S.OpportunityAnalysis(summary="s", claims=[claim("totally invented quote here")])
        assert "previous answer was rejected" in user
        return MockLLM().complete_structured("", user, S.OpportunityAnalysis, 10).parsed

    llm.handlers[S.OpportunityAnalysis] = handler
    out = run_task(gateway(session, live(), llm), session, opp)
    assert out is not None
    outcomes = [
        u.outcome for u in session.execute(select(LlmUsage).order_by(LlmUsage.created_at)).scalars()
    ]
    assert sorted(outcomes) == ["accepted", "rejected"]  # both attempts were paid for and logged


def test_twice_rejected_returns_none_and_is_audited(session) -> None:  # type: ignore[no-untyped-def]
    opp = seed(session)[0]
    llm = RoutingLLM()
    llm.handlers[S.OpportunityAnalysis] = lambda n, u: S.OpportunityAnalysis(
        summary="s", claims=[claim("never in the evidence at all")]
    )
    assert run_task(gateway(session, live(), llm), session, opp) is None
    a = session.execute(select(AiAnalysis)).scalar_one()
    assert a.validated is False and "validation" in (a.rejection_reason or "")
    assert session.execute(select(func.count()).select_from(LlmUsage)).scalar_one() == 2


def test_transient_errors_retry_then_succeed(session) -> None:  # type: ignore[no-untyped-def]
    opp = seed(session)[0]
    llm = RoutingLLM()
    llm.handlers[S.OpportunityAnalysis] = lambda n, u: (
        LLMTransientError("RateLimitError") if n < 3
        else MockLLM().complete_structured("", u, S.OpportunityAnalysis, 10).parsed
    )  # fmt: skip
    out = run_task(gateway(session, live(), llm), session, opp)
    assert out is not None and len(llm.calls) == 3


def test_transient_errors_give_up_after_max_retries(session) -> None:  # type: ignore[no-untyped-def]
    opp = seed(session)[0]
    llm = RoutingLLM()
    llm.handlers[S.OpportunityAnalysis] = lambda n, u: LLMTransientError("Timeout")
    with pytest.raises(LLMTransientError):
        run_task(gateway(session, live(llm_max_retries=3), llm), session, opp)
    assert len(llm.calls) == 3


def test_refusal_is_audited_not_raised(session) -> None:  # type: ignore[no-untyped-def]
    opp = seed(session)[0]
    llm = RoutingLLM()
    llm.handlers[S.OpportunityAnalysis] = lambda n, u: LLMRefusalError("declined")
    assert run_task(gateway(session, live(), llm), session, opp) is None
    assert "refused" in (session.execute(select(AiAnalysis)).scalar_one().rejection_reason or "")


def test_per_run_llm_limit_blocks_before_any_call(session) -> None:  # type: ignore[no-untyped-def]
    opp = seed(session)[0]
    llm = RoutingLLM()
    gw = gateway(session, live(llm_spend_limit_per_run_usd=0.0001, llm_max_output_tokens=1000), llm)
    with pytest.raises(SpendLimitExceeded):
        run_task(gw, session, opp, run_id=uuid.uuid4())
    assert llm.calls == []


# ------------------------------------------------------------------ workflow
def _process(session, settings, llm=None):  # type: ignore[no-untyped-def]
    enqueue_run(session, "full_discovery", {"query": {}})
    run = claim_next_run(session)
    assert run is not None
    return execute_run(
        session,
        run.id,
        build_connectors(["mock_opportunities", "mock_agencies"], settings),
        settings,
        llm=llm,
    )


def test_mock_mode_runs_full_ai_pipeline_free(session, settings) -> None:  # type: ignore[no-untyped-def]
    assert settings.llm_mock_mode is True
    run = _process(session, settings)
    assert run.status == "succeeded" and run.state["ai"]["considered"] > 0
    tasks = {a.task for a in session.execute(select(AiAnalysis)).scalars()}
    assert tasks == {
        "opportunity_analysis", "requirement_extraction", "evidence_evaluation",
        "research_brief", "qualification", "outreach_draft",
    }  # fmt: skip
    assert all(a.validated for a in session.execute(select(AiAnalysis)).scalars())
    assert all(float(u.cost_usd) == 0 for u in session.execute(select(LlmUsage)).scalars())
    assert session.execute(select(func.count()).select_from(ProviderSpend)).scalar_one() == 0
    q = (
        session.execute(select(Qualification).where(Qualification.criteria.isnot(None)))
        .scalars()
        .all()
    )
    assert any("ai_fit_score" in x.criteria for x in q)
    drafts = session.execute(select(OutreachDraft)).scalars().all()
    assert drafts and all(
        d.status == "pending_approval" and "[YOUR RELEVANT EXPERIENCE]" in d.body for d in drafts
    )
    assert run.state["metrics"]["llm_input_tokens"] > 0


def test_live_mode_without_approval_makes_no_calls(session) -> None:  # type: ignore[no-untyped-def]
    s = Settings(database_url="sqlite://", llm_mock_mode=False)  # paid calls NOT enabled
    llm = RoutingLLM()
    llm.ensure_ready = lambda: (_ for _ in ()).throw(LLMDisabledError("disabled"))  # type: ignore[method-assign]
    run = _process(session, s, llm)
    assert run.status == "succeeded" and "disabled" in run.state["ai"]["skipped"]
    assert llm.calls == []
    assert session.execute(select(func.count()).select_from(AiAnalysis)).scalar_one() == 0
    assert (
        session.execute(select(func.count()).select_from(OutreachDraft)).scalar_one() > 0
    )  # template fallback


def test_ai_unsupported_verdict_blocks_qualification(session) -> None:  # type: ignore[no-untyped-def]
    llm = RoutingLLM(is_mock=True, tokens=(10, 10))
    llm.provider = "mock"
    llm.handlers[S.EvidenceEvaluation] = lambda n, u: S.EvidenceEvaluation(
        verdict="unsupported", reliability=0.1, issues=["does not state a need"], supporting=[]
    )
    run = _process(session, Settings(database_url="sqlite://"), llm)
    analyzed = {uuid.UUID(k) for k in run.state["ai_results"]}
    assert analyzed and all(v["verdict"] == "unsupported" for v in run.state["ai_results"].values())
    for opp in session.execute(select(Opportunity).where(Opportunity.id.in_(analyzed))).scalars():
        assert opp.status != "qualified"  # AI said the evidence does not support it
    rows = session.execute(select(Qualification)).scalars().all()
    assert any("does not support" in r.rationale for r in rows)


def test_ai_stage_is_capped_per_run(session) -> None:  # type: ignore[no-untyped-def]
    s = Settings(database_url="sqlite://", ai_max_opportunities_per_run=1)
    run = _process(session, s)
    assert run.state["ai"]["considered"] == 1


def test_budget_exhaustion_degrades_to_deterministic(session) -> None:  # type: ignore[no-untyped-def]
    llm = RoutingLLM()  # priced, non-mock
    s = live(llm_spend_limit_per_run_usd=0.003, llm_max_output_tokens=400)
    run = _process(session, s, llm)
    assert run.status == "succeeded"
    assert run.state["ai"]["stopped"]
    total = session.execute(select(func.sum(LlmUsage.cost_usd))).scalar_one()
    assert float(total) <= 0.003 + 1e-9  # never exceeds the per-run LLM limit


# ------------------------------------------------------------------ OpenAI client mapping
class FakeResponses:
    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.result, self.error, self.kwargs, self.n = result, error, {}, 0

    def parse(self, **kwargs: Any) -> Any:
        self.n += 1
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.result


class NS:
    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


def fake_client(responses: FakeResponses) -> Any:
    return NS(responses=responses)


def ok_response() -> Any:
    parsed = S.ResearchBrief(findings=[], open_questions=[])
    return NS(
        status="completed",
        output=[],
        output_parsed=parsed,
        usage=NS(input_tokens=11, output_tokens=7),
    )


def make_llm(resp: FakeResponses, **kw: Any) -> OpenAILLM:
    args: dict[str, Any] = dict(
        api_key=None, model="m", paid_calls_enabled=True, client=fake_client(resp)
    )
    args.update(kw)
    return OpenAILLM(**args)


def test_openai_uses_responses_parse_with_strict_schema_and_reports_usage() -> None:
    fr = FakeResponses(ok_response())
    out = make_llm(fr).complete_structured("SYS", "USER", S.ResearchBrief, 1000)
    assert (out.input_tokens, out.output_tokens, out.provider, out.model) == (11, 7, "openai", "m")
    assert fr.kwargs["text_format"] is S.ResearchBrief
    assert fr.kwargs["instructions"] == "SYS" and fr.kwargs["input"] == "USER"
    assert fr.kwargs["max_output_tokens"] == 1000 and fr.kwargs["store"] is False


def test_openai_never_calls_when_not_enabled() -> None:
    for kw, needle in (
        ({"paid_calls_enabled": False}, "disabled"),
        ({"model": ""}, "OPENAI_MODEL"),
    ):
        fr = FakeResponses(ok_response())
        with pytest.raises(LLMDisabledError, match=needle):
            make_llm(fr, **kw).complete_structured("s", "u", S.ResearchBrief, 100)
        assert fr.n == 0
    with pytest.raises(LLMDisabledError, match="OPENAI_API_KEY"):
        OpenAILLM(None, "m", True).complete_structured("s", "u", S.ResearchBrief, 100)


def test_openai_error_mapping() -> None:
    req = httpx.Request("POST", "https://api.openai.com/v1/responses")
    rate = openai.RateLimitError("slow down", response=httpx.Response(429, request=req), body=None)
    bad = openai.BadRequestError("bad", response=httpx.Response(400, request=req), body=None)
    with pytest.raises(LLMTransientError):
        make_llm(FakeResponses(error=rate)).complete_structured("s", "u", S.ResearchBrief, 100)
    with pytest.raises(LLMRefusalError, match="400"):
        make_llm(FakeResponses(error=bad)).complete_structured("s", "u", S.ResearchBrief, 100)
    incomplete = NS(status="incomplete", output=[], output_parsed=None, usage=None)
    with pytest.raises(LLMRefusalError, match="incomplete"):
        make_llm(FakeResponses(incomplete)).complete_structured("s", "u", S.ResearchBrief, 100)
    refusal = NS(status="completed", usage=None, output_parsed=None,
                 output=[NS(content=[NS(type="refusal", refusal="I can't help")])])  # fmt: skip
    with pytest.raises(LLMRefusalError, match="refused"):
        make_llm(FakeResponses(refusal)).complete_structured("s", "u", S.ResearchBrief, 100)
    empty = NS(status="completed", output=[], output_parsed=None, usage=None)
    with pytest.raises(LLMRefusalError, match="no parsable"):
        make_llm(FakeResponses(empty)).complete_structured("s", "u", S.ResearchBrief, 100)


def test_factory_defaults_and_cost_math() -> None:
    assert Settings().llm_provider == "openai" and build_llm(Settings()).is_mock
    live_llm = build_llm(Settings(llm_mock_mode=False))
    assert live_llm.provider == "openai" and not live_llm.is_mock
    with pytest.raises(LLMDisabledError):
        live_llm.ensure_ready()  # paid calls off by default
    with pytest.raises(ValueError):
        build_llm(Settings(llm_mock_mode=False, llm_provider="other"))
    assert math.isclose(1000 * 1.0 / 1e6 + 500 * 2.0 / 1e6, 0.002)
