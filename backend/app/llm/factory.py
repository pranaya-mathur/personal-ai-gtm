from app.core.config import Settings
from app.llm.base import LLMClient
from app.llm.mock import MockLLM
from app.llm.openai_client import OpenAILLM


def build_llm(settings: Settings) -> LLMClient:
    if settings.llm_mock_mode:
        return MockLLM()
    if settings.llm_provider == "openai":
        return OpenAILLM(
            settings.openai_api_key, settings.openai_model, settings.llm_paid_calls_enabled
        )
    raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider!r} (supported: 'openai')")
