"""Provider-neutral LLM boundary. Agents depend only on this Protocol."""

from typing import Protocol

from pydantic import BaseModel


class LLMDisabledError(Exception):
    """Raised instead of making a paid call when it has not been explicitly enabled."""


class LLMResponse(BaseModel):
    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


class LLMClient(Protocol):
    provider: str

    def complete(self, system: str, prompt: str, max_tokens: int = 1024) -> LLMResponse: ...
