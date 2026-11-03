"""Anthropic Claude client (intended primary provider).

Paid calls are OFF by default and require ALL of: LLM_PAID_CALLS_ENABLED=true, a developer-API
key (ANTHROPIC_API_KEY from console.anthropic.com), and an explicit ANTHROPIC_MODEL.
A Claude Pro/Max subscription or Claude Code login is NOT an API credential for a deployed app.
The `anthropic` SDK is an optional dependency (`uv sync --group llm`) and is imported lazily.
"""

from typing import Any

from pydantic import SecretStr

from app.llm.base import LLMDisabledError, LLMResponse


class AnthropicLLM:
    provider = "anthropic"

    def __init__(self, api_key: SecretStr | None, model: str, paid_calls_enabled: bool) -> None:
        self._api_key = api_key
        self.model = model
        self._enabled = paid_calls_enabled

    def _assert_ready(self) -> None:
        if not self._enabled:
            raise LLMDisabledError("Paid LLM calls are disabled (LLM_PAID_CALLS_ENABLED=false)")
        if not self._api_key or not self._api_key.get_secret_value():
            raise LLMDisabledError("ANTHROPIC_API_KEY is not set")
        if not self.model:
            raise LLMDisabledError("ANTHROPIC_MODEL must be set explicitly")

    def complete(self, system: str, prompt: str, max_tokens: int = 1024) -> LLMResponse:
        self._assert_ready()
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise LLMDisabledError("Install the SDK: uv sync --group llm") from exc
        assert self._api_key is not None
        client = anthropic.Anthropic(api_key=self._api_key.get_secret_value())
        msg: Any = client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        return LLMResponse(
            text=text,
            provider=self.provider,
            model=self.model,
            input_tokens=msg.usage.input_tokens,
            output_tokens=msg.usage.output_tokens,
        )
