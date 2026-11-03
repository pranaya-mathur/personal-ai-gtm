"""The owner's capability profile and the (deterministic) match against an opportunity.

The seven areas below are the areas the owner stated. A project REFERENCE only exists when the
owner adds an approved, verifiable one; until then every area is "claimed, no verified project on
file" and outreach/proposals must say so with a placeholder. Nothing here invents experience.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CapabilityProfile

DEFAULT_AREAS: list[dict[str, Any]] = [
    {"key": "agentic_ai", "label": "Agentic AI and multi-agent systems", "projects": [],
     "terms": ["AI agent", "AI agents", "agentic", "multi-agent", "autonomous agent", "tool use"]},
    {"key": "rag", "label": "RAG and enterprise knowledge assistants", "projects": [],
     "terms": ["RAG", "retrieval-augmented", "retrieval augmented", "knowledge base", "semantic search", "knowledge assistant", "chatbot", "virtual assistant", "KI-Chatbot"]},
    {"key": "document_intelligence", "label": "Document intelligence and extraction", "projects": [],
     "terms": ["document processing", "document intelligence", "OCR", "invoice processing", "extraction", "document automation", "intelligent document"]},
    {"key": "workflow_automation", "label": "Workflow and process automation", "projects": [],
     "terms": ["workflow automation", "process automation", "intelligent automation", "RPA", "automate", "automation"]},
    {"key": "custom_ai_engineering", "label": "Custom AI/ML engineering (LLM apps, ML models)", "projects": [],
     "terms": ["LLM", "large language model", "generative AI", "GenAI", "machine learning", "ML model", "fine-tuning", "prototype", "MVP", "artificial intelligence"]},
    {"key": "cloud_integration", "label": "Cloud deployment and system integration", "projects": [],
     "terms": ["AWS", "Azure", "GCP", "cloud", "API integration", "CRM integration", "integration", "deploy"]},
    {"key": "ai_governance", "label": "Enterprise AI governance and risk", "projects": [],
     "terms": ["AI governance", "responsible AI", "AI risk", "compliance", "AI Act", "model governance", "audit"]},
]  # fmt: skip


def get_profile(session: Session) -> list[dict[str, Any]]:
    row = session.execute(select(CapabilityProfile).limit(1)).scalars().first()
    return list(row.areas) if row and row.areas else [dict(a) for a in DEFAULT_AREAS]


def save_profile(session: Session, areas: list[dict[str, Any]], who: str) -> list[dict[str, Any]]:
    clean: list[dict[str, Any]] = []
    for a in areas:
        key, label = str(a.get("key", "")).strip(), str(a.get("label", "")).strip()
        if not key or not label:
            raise ValueError("every capability area needs a key and a label")
        projects = []
        for p in a.get("projects") or []:
            if not (p.get("title") and p.get("verifiable_reference")):
                raise ValueError(
                    f"project in {key!r} needs a title and a verifiable_reference (link or contact)"
                )
            projects.append(
                {
                    "title": str(p["title"]).strip(),
                    "verifiable_reference": str(p["verifiable_reference"]).strip(),
                    "summary": str(p.get("summary") or "").strip(),
                }
            )
        clean.append(
            {
                "key": key,
                "label": label,
                "terms": [str(t) for t in a.get("terms") or []],
                "projects": projects,
            }
        )
    row = session.execute(select(CapabilityProfile).limit(1)).scalars().first()
    if row is None:
        row = CapabilityProfile(areas=clean, updated_by=who)
        session.add(row)
    else:
        row.areas, row.updated_by = clean, who
    session.flush()
    return clean


def _has(term: str, text: str) -> bool:
    flags = 0 if (len(term) <= 4 and term.isupper()) else re.I
    return bool(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, flags))


def match_capabilities(text: str, areas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for a in areas:
        hits = [t for t in a.get("terms", []) if _has(t, text)]
        if hits:
            verified = bool(a.get("projects"))
            out.append(
                {
                    "key": a["key"],
                    "label": a["label"],
                    "matched_terms": hits[:4],
                    "evidence_status": (
                        f"{len(a['projects'])} verified project reference(s) on file"
                        if verified
                        else "owner-claimed area; NO verified project reference on file"
                    ),
                    "projects": a.get("projects", []),
                }
            )
    return out
