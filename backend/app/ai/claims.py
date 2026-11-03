"""Source-backed claim enforcement. Nothing AI-generated is accepted on trust."""

import re

from pydantic import BaseModel

from app.ai.schemas import (
    Claim,
    EvidenceEvaluation,
    OpportunityDossier,
    OutreachDraftAI,
    QualificationAssessment,
)

MIN_QUOTE_CHARS = 8
_URL = re.compile(r"https?://[^\s)>\]\"']+")
_MONEY = re.compile(r"[$€£]\s?\d[\d,.]*(?:\s?[kKmM])?|\d[\d,.]*\s?(?:USD|EUR|GBP)\b")


class ClaimValidationError(Exception):
    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w$€£%.@:/-]+", " ", text.lower()).split())


def collect_claims(obj: object) -> list[Claim]:
    if isinstance(obj, Claim):
        return [obj]
    found: list[Claim] = []
    if isinstance(obj, BaseModel):
        for name in type(obj).model_fields:
            found += collect_claims(getattr(obj, name))
    elif isinstance(obj, list):
        for item in obj:
            found += collect_claims(item)
    return found


def validate_output(
    output: BaseModel,
    evidence: dict[str, str],
    min_claims: int = 0,
    evidence_urls: set[str] | None = None,
) -> None:
    """Raises ClaimValidationError listing every problem. `evidence` maps id -> original text."""
    problems: list[str] = []
    claims = collect_claims(output)
    if len(claims) < min_claims:
        problems.append(f"needs at least {min_claims} source-backed claim(s), got {len(claims)}")
    normalized = {k: norm(v) for k, v in evidence.items()}
    for c in claims:
        if not c.text.strip():
            problems.append("claim with empty text")
        if c.evidence_id not in normalized:
            problems.append(f"unknown evidence_id {c.evidence_id!r}")
            continue
        q = norm(c.quote)
        if len(q) < MIN_QUOTE_CHARS:
            problems.append(f"quote too short to support claim {c.text[:40]!r}")
        elif q not in normalized[c.evidence_id]:
            problems.append(f"quote not found verbatim in evidence for claim {c.text[:40]!r}")

    if isinstance(output, EvidenceEvaluation) and not 0 <= output.reliability <= 1:
        problems.append("reliability outside 0..1")
    if isinstance(output, OpportunityDossier):
        if not 0 <= output.reliability <= 1:
            problems.append("reliability outside 0..1")
        if not 0 <= output.fit_score <= 100:
            problems.append("fit_score outside 0..100")
        if output.ai_project == "explicit" and output.ai_project_support is None:
            problems.append("ai_project 'explicit' requires ai_project_support quote")
    if isinstance(output, QualificationAssessment):
        if not 0 <= output.fit_score <= 100:
            problems.append("fit_score outside 0..100")
        problems += [
            f"criterion {c.name!r} score outside 0..100"
            for c in output.criteria
            if not 0 <= c.score <= 100
        ]
    if isinstance(output, OutreachDraftAI):
        corpus = " ".join(evidence.values())
        allowed = {u.rstrip("/").lower() for u in (evidence_urls or set())}
        for url in _URL.findall(output.body):
            if url.rstrip("/.,").lower() not in allowed:
                problems.append(f"body contains a URL not present in evidence: {url[:60]}")
        corpus_n = norm(corpus)
        for money in _MONEY.findall(output.body):
            if norm(money) not in corpus_n:
                problems.append(f"body states an amount not present in evidence: {money!r}")
    if problems:
        raise ClaimValidationError(problems)
