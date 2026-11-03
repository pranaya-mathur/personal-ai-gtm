"""Deterministic project-type tag (for filtering only; it never affects qualification)."""

import re

_TYPES: list[tuple[str, re.Pattern[str]]] = [
    (
        "rag_knowledge",
        re.compile(r"\b(RAG|retrieval[- ]augmented|knowledge ?base|semantic search)\b", re.I),
    ),
    ("ai_agents", re.compile(r"\b(AI agents?|agentic|multi-agent|autonomous agents?)\b", re.I)),
    (
        "chatbot_support",
        re.compile(r"\b(chat-?bots?|virtual assistant|conversational|customer support)\b", re.I),
    ),
    (
        "document_intelligence",
        re.compile(
            r"\b(document (?:processing|intelligence|extraction)|OCR|invoice processing)\b", re.I
        ),
    ),
    (
        "workflow_automation",
        re.compile(
            r"\b(workflow automation|intelligent automation|process automation|RPA)\b", re.I
        ),
    ),
    (
        "llm_integration",
        re.compile(r"\b(LLM|large language model|generative AI|GenAI|GPT|OpenAI)\b", re.I),
    ),
    (
        "computer_vision",
        re.compile(r"\b(computer vision|image recognition|video analytics)\b", re.I),
    ),
    (
        "ml_predictive",
        re.compile(r"\b(machine learning|predictive|forecasting model|deep learning)\b", re.I),
    ),
]


def project_type(text: str) -> str:
    for name, rx in _TYPES:
        if rx.search(text):
            return name
    return "general_ai" if re.search(r"\b(AI|artificial intelligence)\b", text) else "other"
