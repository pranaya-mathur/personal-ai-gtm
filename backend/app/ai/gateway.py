"""Every LLM call goes through here: availability gate, pricing, spend limits, retries, token/cost
ledger, claim validation (with one metered repair attempt), and audit storage."""

import hashlib
import math
import uuid
from collections.abc import Callable

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.ai.claims import ClaimValidationError
from app.core.config import Settings
from app.core.logging import get_logger
from app.llm.base import LLMClient, LLMDisabledError, LLMRefusalError, LLMTransientError, T
from app.models import AiAnalysis, LlmUsage
from app.services.spend import SpendGuard, SpendLimitExceeded

log = get_logger(__name__)
Validator = Callable[[BaseModel], list[str] | None]


class RecordBudgetExceeded(SpendLimitExceeded):
    """A single record hit its call or cost allowance; the run may continue with other records."""


class LLMGateway:
    def __init__(
        self, session: Session, settings: Settings, client: LLMClient, spend: SpendGuard
    ) -> None:
        self.session, self.settings, self.client, self.spend = session, settings, client, spend

    # ---- availability & pricing -------------------------------------------------------
    def availability(self) -> tuple[bool, str]:
        try:
            self.client.ensure_ready()
            self._prices()
        except LLMDisabledError as exc:
            return False, str(exc)
        return True, "ready"

    def _prices(self) -> tuple[float, float]:
        if self.client.is_mock:
            return 0.0, 0.0
        s = self.settings
        if s.openai_input_usd_per_mtok is None or s.openai_output_usd_per_mtok is None:
            raise LLMDisabledError(
                "OPENAI_INPUT_USD_PER_MTOK / OPENAI_OUTPUT_USD_PER_MTOK must be set "
                "so cost can be tracked and limited"
            )
        return s.openai_input_usd_per_mtok, s.openai_output_usd_per_mtok

    def cost_usd(self, input_tokens: int, output_tokens: int) -> float:
        p_in, p_out = self._prices()
        return (input_tokens * p_in + output_tokens * p_out) / 1_000_000

    def estimate_usd(self, system: str, user: str, max_output_tokens: int) -> float:
        est_input = math.ceil((len(system) + len(user)) / 3)  # conservative chars-per-token
        return self.cost_usd(est_input, max_output_tokens)

    def run_spent_usd(self, run_id: uuid.UUID | None) -> float:
        if run_id is None:
            return 0.0
        total = self.session.execute(
            select(func.coalesce(func.sum(LlmUsage.cost_usd), 0)).where(LlmUsage.run_id == run_id)
        ).scalar_one()
        return float(total)

    # ---- the call -----------------------------------------------------------------------
    def input_hash(
        self, task: str, model: str, schema: type[BaseModel], system: str, user: str
    ) -> str:
        raw = "\x1f".join([task, model, schema.__name__, system, user])
        return hashlib.sha256(raw.encode()).hexdigest()

    def _cached(self, h: str, schema: type[T]) -> T | None:
        row = (
            self.session.execute(
                select(AiAnalysis)
                .where(AiAnalysis.input_hash == h, AiAnalysis.validated.is_(True))
                .order_by(AiAnalysis.created_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        if row is None or row.output is None:
            return None
        try:
            return schema.model_validate(row.output)
        except ValueError:
            return None

    def _check_allowance(
        self, task: str, opportunity_id: uuid.UUID, run_id: uuid.UUID | None, est: float
    ) -> None:
        if run_id is None:
            return
        calls, spent = self.session.execute(
            select(func.count(), func.coalesce(func.sum(LlmUsage.cost_usd), 0)).where(
                LlmUsage.run_id == run_id,
                LlmUsage.opportunity_id == opportunity_id,
                LlmUsage.outcome != "cached",
            )
        ).one()
        if calls >= self.settings.ai_max_calls_per_record:
            raise RecordBudgetExceeded(f"record call allowance ({calls}) reached")
        if float(spent) + est > self.settings.ai_record_budget_usd:
            raise RecordBudgetExceeded("per-record AI budget would be exceeded")
        task_spent = self.session.execute(
            select(func.coalesce(func.sum(LlmUsage.cost_usd), 0)).where(
                LlmUsage.run_id == run_id, LlmUsage.task == task
            )
        ).scalar_one()
        if float(task_spent) + est > self.settings.ai_task_budget_usd:
            raise SpendLimitExceeded(f"per-task AI budget for {task!r} would be exceeded")

    def run_task(
        self,
        *,
        task: str,
        schema: type[T],
        system: str,
        user: str,
        run_id: uuid.UUID | None,
        opportunity_id: uuid.UUID,
        validator: Validator,
        agent: str = "unknown",
    ) -> T | None:
        """Returns a validated model, or None if the output was refused/rejected (audited).
        Identical inputs are served from the cache without a model call."""
        model = self.settings.model_for(task) or self.client.model
        h = self.input_hash(task, model, schema, system, user)
        hit = self._cached(h, schema)
        if hit is not None:
            self._ledger_cached(task, opportunity_id, run_id, agent, model)
            self._store(task, opportunity_id, run_id, hit, True, "cache hit", None, model, h)
            return hit
        max_out = self.settings.max_output_for(task)
        prompt = user
        last_reason = ""
        for attempt in range(1 + max(0, self.settings.ai_max_repair_attempts)):
            est = self.estimate_usd(system, prompt, max_out)
            self._check_allowance(task, opportunity_id, run_id, est)
            if self.run_spent_usd(run_id) + est > self.settings.llm_spend_limit_per_run_usd:
                raise SpendLimitExceeded("per-run LLM spend limit would be exceeded")
            reservation = None
            if self.client.is_mock:
                self.spend.check(est, run_id, None)
            else:
                reservation = self.spend.reserve(
                    est, run_id, "openai", f"openai:{model}", f"llm:{task}"
                )
            try:
                result = self._call_with_retries(task, system, prompt, schema, max_out)
            except LLMRefusalError as exc:  # usage unknown: the reservation stays (conservative)
                self._store(task, opportunity_id, run_id, None, False, f"refused: {exc}")
                self._note_failure(task, opportunity_id, run_id, agent, model, f"refused: {exc}")
                return None
            cost = self.cost_usd(result.input_tokens, result.output_tokens)
            if reservation is not None:
                self.spend.settle(reservation, cost)
            review: list[str] = []
            try:
                review = validator(result.parsed) or []
            except ClaimValidationError as exc:
                last_reason = str(exc)
                self._ledger(
                    task, opportunity_id, run_id, result, cost, "rejected", agent, last_reason
                )
                log.warning("ai_output_rejected", task=task, attempt=attempt, reason=last_reason)
                prompt = (
                    f"{user}\n\nYour previous answer was rejected: {last_reason}. Quote evidence "
                    "verbatim, use only provided evidence_ids, or remove the unsupported claim."
                )
                continue
            self._ledger(task, opportunity_id, run_id, result, cost, "accepted", agent, None)
            self._store(
                task,
                opportunity_id,
                run_id,
                result.parsed,
                True,
                f"review: {len(review)} ambiguous claim(s): " + " | ".join(review)[:400]
                if review
                else None,
                result.provider,
                result.model,
                h,
            )
            assert isinstance(result.parsed, schema)
            return result.parsed
        self._store(task, opportunity_id, run_id, None, False, f"validation: {last_reason}")
        return None

    def _call_with_retries(self, task: str, system: str, user: str, schema: type[T], max_out: int):  # type: ignore[no-untyped-def]
        for attempt in Retrying(
            retry=retry_if_exception_type(LLMTransientError),
            stop=stop_after_attempt(self.settings.llm_max_retries),
            wait=wait_exponential(multiplier=self.settings.llm_retry_base_seconds, max=20),
            reraise=True,
        ):
            with attempt:
                return self.client.complete_structured(
                    system, user, schema, max_out, self.settings.model_for(task) or None
                )

    # ---- persistence ----------------------------------------------------------------------
    def _ledger_cached(self, task, opportunity_id, run_id, agent, model) -> None:  # type: ignore[no-untyped-def]
        self.session.add(
            LlmUsage(
                run_id=run_id,
                opportunity_id=opportunity_id,
                task=task,
                provider=self.client.provider,
                model=model,
                input_tokens=0,
                output_tokens=0,
                cost_usd=0,
                outcome="cached",
                agent=agent,
                reason=None,
            )
        )
        self.session.flush()

    def _note_failure(self, task, opportunity_id, run_id, agent, model, reason) -> None:  # type: ignore[no-untyped-def]
        """A refused/unparseable call still consumed tokens we cannot see; record the event."""
        self.session.add(
            LlmUsage(
                run_id=run_id,
                opportunity_id=opportunity_id,
                task=task,
                provider=self.client.provider,
                model=model,
                input_tokens=0,
                output_tokens=0,
                cost_usd=0,
                outcome="failed",
                agent=agent,
                reason=reason[:500],
            )
        )
        self.session.flush()

    def _ledger(  # type: ignore[no-untyped-def]
        self, task, opportunity_id, run_id, result, cost, outcome, agent="unknown", reason=None
    ) -> None:
        self.session.add(
            LlmUsage(
                run_id=run_id,
                opportunity_id=opportunity_id,
                task=task,
                provider=result.provider,
                model=result.model,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                cost_usd=cost,
                outcome=outcome,
                agent=agent,
                reason=(reason or None) and reason[:500],
            )
        )
        self.session.flush()

    def _store(  # type: ignore[no-untyped-def]
        self,
        task,
        opportunity_id,
        run_id,
        parsed,
        validated,
        reason,
        provider=None,
        model=None,
        input_hash=None,
    ) -> None:
        self.session.add(
            AiAnalysis(
                opportunity_id=opportunity_id,
                run_id=run_id,
                task=task,
                provider=provider or self.client.provider,
                model=model or self.client.model,
                output=parsed.model_dump() if parsed is not None else None,
                validated=validated,
                rejection_reason=reason,
                input_hash=input_hash,
            )
        )
        self.session.flush()
