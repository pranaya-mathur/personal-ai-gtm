"""OpenAI client using the Responses API with Structured Outputs.

Contract (verified against OpenAI docs and the installed SDK types): `client.responses.parse(
model=, instructions=, input=, text_format=<PydanticModel>, max_output_tokens=)` returns an object
with `.output_parsed`, `.status`, `.usage.input_tokens/.output_tokens`; a refusal appears as a
`ResponseOutputRefusal` content item. Nothing is sent unless ensure_ready() passes.
"""

from typing import Any

import openai
from pydantic import SecretStr

from app.llm.base import (
    LLMDisabledError,
    LLMRefusalError,
    LLMTransientError,
    StructuredResult,
    T,
)

_TRANSIENT = (
    openai.RateLimitError,
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.InternalServerError,
)


class OpenAILLM:
    provider = "openai"
    is_mock = False

    def __init__(
        self,
        api_key: SecretStr | None,
        model: str,
        paid_calls_enabled: bool,
        client: Any | None = None,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self._enabled = paid_calls_enabled
        self._client = client

    def ensure_ready(self) -> None:
        if not self._enabled:
            raise LLMDisabledError("Paid LLM calls are disabled (LLM_PAID_CALLS_ENABLED=false)")
        if self._client is None and not (self._api_key and self._api_key.get_secret_value()):
            raise LLMDisabledError("OPENAI_API_KEY is not set")
        if not self.model:
            raise LLMDisabledError("OPENAI_MODEL must be set explicitly")

    def _get_client(self) -> Any:
        if self._client is None:
            assert self._api_key is not None
            # SDK retries disabled: retries are owned by LLMGateway so every attempt is metered.
            self._client = openai.OpenAI(api_key=self._api_key.get_secret_value(), max_retries=0)
        return self._client

    def complete_structured(
        self,
        system: str,
        user: str,
        schema: type[T],
        max_output_tokens: int,
        model: str | None = None,
    ) -> StructuredResult:
        self.ensure_ready()
        model = model or self.model
        if not model:
            raise LLMDisabledError("no model configured for this task")
        try:
            resp = self._get_client().responses.parse(
                model=model,
                instructions=system,
                input=user,
                text_format=schema,
                max_output_tokens=max(16, max_output_tokens),
                store=False,
            )
        except _TRANSIENT as exc:
            raise LLMTransientError(type(exc).__name__) from exc
        except openai.APIStatusError as exc:
            raise LLMRefusalError(f"OpenAI API error {exc.status_code}") from exc
        except (openai.LengthFinishReasonError, openai.ContentFilterFinishReasonError) as exc:
            raise LLMRefusalError(type(exc).__name__) from exc

        if getattr(resp, "status", "completed") == "incomplete":
            raise LLMRefusalError("response incomplete (max_output_tokens reached)")
        for item in getattr(resp, "output", []) or []:
            for part in getattr(item, "content", []) or []:
                if getattr(part, "type", "") == "refusal":
                    raise LLMRefusalError(f"model refused: {getattr(part, 'refusal', '')[:200]}")
        parsed = getattr(resp, "output_parsed", None)
        if parsed is None:
            raise LLMRefusalError("no parsable output")
        usage = resp.usage
        return StructuredResult(
            parsed=parsed,
            provider=self.provider,
            model=model,
            input_tokens=int(usage.input_tokens),
            output_tokens=int(usage.output_tokens),
        )
