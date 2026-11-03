"""Provider-neutral structured-output LLM boundary. Agents never import a vendor SDK."""

from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    pass


class LLMDisabledError(LLMError):
    """Raised instead of making a paid call when it has not been explicitly enabled."""


class LLMTransientError(LLMError):
    """Rate limit / timeout / 5xx: safe to retry with backoff."""


class LLMRefusalError(LLMError):
    """The model declined, or returned nothing that parses to the schema."""


class StructuredResult(BaseModel):
    parsed: BaseModel
    provider: str
    model: str
    input_tokens: int
    output_tokens: int


class LLMClient(Protocol):
    provider: str
    model: str
    is_mock: bool

    def ensure_ready(self) -> None:
        """Raise LLMDisabledError unless a call is currently permitted. Makes no network call."""
        ...

    def complete_structured(
        self, system: str, user: str, schema: type[T], max_output_tokens: int
    ) -> StructuredResult: ...
