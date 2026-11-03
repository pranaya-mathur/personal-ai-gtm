from app.llm.base import LLMResponse


class MockLLM:
    """Deterministic, offline, free. Echoes a fixed marker so tests can assert on it."""

    provider = "mock"

    def complete(self, system: str, prompt: str, max_tokens: int = 1024) -> LLMResponse:
        return LLMResponse(text="[MOCK LLM RESPONSE]", provider=self.provider, model="mock")
