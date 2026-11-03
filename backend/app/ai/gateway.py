"""Every LLM call goes through here: availability gate, pricing, spend limits, retries, token/cost
ledger, claim validation (with one metered repair attempt), and audit storage."""

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
from app.models import AiAnalysis, LlmUsage, ProviderSpend
from app.services.spend import SpendGuard, SpendLimitExceeded

log = get_logger(__name__)
MAX_VALIDATION_ATTEMPTS = 2  # initial + one repair
Validator = Callable[[BaseModel], None]


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
    ) -> T | None:
        """Returns a validated model, or None if the output was refused/rejected (audited)."""
        max_out = self.settings.llm_max_output_tokens
        prompt = user
        last_reason = ""
        for attempt in range(MAX_VALIDATION_ATTEMPTS):
            est = self.estimate_usd(system, prompt, max_out)
            if self.run_spent_usd(run_id) + est > self.settings.llm_spend_limit_per_run_usd:
                raise SpendLimitExceeded("per-run LLM spend limit would be exceeded")
            self.spend.check(est, run_id)
            try:
                result = self._call_with_retries(system, prompt, schema, max_out)
            except LLMRefusalError as exc:
                self._store(task, opportunity_id, run_id, None, False, f"refused: {exc}")
                return None
            cost = self.cost_usd(result.input_tokens, result.output_tokens)
            try:
                validator(result.parsed)
            except ClaimValidationError as exc:
                last_reason = str(exc)
                self._ledger(task, opportunity_id, run_id, result, cost, "rejected")
                log.warning("ai_output_rejected", task=task, attempt=attempt, reason=last_reason)
                prompt = (
                    f"{user}\n\nYour previous answer was rejected: {last_reason}. Quote evidence "
                    "verbatim, use only provided evidence_ids, or remove the unsupported claim."
                )
                continue
            self._ledger(task, opportunity_id, run_id, result, cost, "accepted")
            self._store(
                task,
                opportunity_id,
                run_id,
                result.parsed,
                True,
                None,
                result.provider,
                result.model,
            )
            assert isinstance(result.parsed, schema)
            return result.parsed
        self._store(task, opportunity_id, run_id, None, False, f"validation: {last_reason}")
        return None

    def _call_with_retries(self, system: str, user: str, schema: type[T], max_out: int):  # type: ignore[no-untyped-def]
        for attempt in Retrying(
            retry=retry_if_exception_type(LLMTransientError),
            stop=stop_after_attempt(self.settings.llm_max_retries),
            wait=wait_exponential(multiplier=self.settings.llm_retry_base_seconds, max=20),
            reraise=True,
        ):
            with attempt:
                return self.client.complete_structured(system, user, schema, max_out)

    # ---- persistence ----------------------------------------------------------------------
    def _ledger(self, task, opportunity_id, run_id, result, cost, outcome) -> None:  # type: ignore[no-untyped-def]
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
            )
        )
        if cost > 0:  # shared ledger so global daily/monthly limits include LLM spend
            self.session.add(
                ProviderSpend(
                    provider=f"{result.provider}:{result.model}",
                    run_id=run_id,
                    amount_usd=math.ceil(cost * 10_000) / 10_000,
                    description=f"llm:{task}",
                )
            )
        self.session.flush()

    def _store(  # type: ignore[no-untyped-def]
        self, task, opportunity_id, run_id, parsed, validated, reason, provider=None, model=None
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
            )
        )
        self.session.flush()
