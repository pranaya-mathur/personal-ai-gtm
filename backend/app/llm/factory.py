from app.core.config import Settings
from app.llm.anthropic_client import AnthropicLLM
from app.llm.base import LLMClient
from app.llm.mock import MockLLM


def build_llm(settings: Settings) -> LLMClient:
    if settings.llm_provider == "mock":
        return MockLLM()
    if settings.llm_provider == "anthropic":
        return AnthropicLLM(
            settings.anthropic_api_key,
            settings.anthropic_model,
            settings.llm_paid_calls_enabled,
        )
    raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider!r} (use 'mock' or 'anthropic')")
