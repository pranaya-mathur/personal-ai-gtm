from typing import Protocol


class LLMClient(Protocol):
    """Boundary for OpenAI. The concrete client arrives in a later milestone; agents in
    Milestone 1 are deterministic and never call a model."""

    def complete(self, system: str, prompt: str) -> str: ...


class NullLLM:
    def complete(self, system: str, prompt: str) -> str:
        raise NotImplementedError("No LLM client configured (Milestone 1 is deterministic)")
