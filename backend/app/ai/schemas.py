"""Strict output schemas for every AI task.

Kept deliberately simple (all fields required, no JSON-schema constraint keywords) so they are
valid for OpenAI strict Structured Outputs. Range and grounding rules are enforced afterwards in
`ai/claims.py`, never trusted to the model.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Claim(_Strict):
    """A commercial/factual statement. Must be backed by a verbatim quote from one evidence item."""

    text: str
    evidence_id: str
    quote: str


class OpportunityAnalysis(_Strict):
    summary: str
    claims: list[Claim]


class RequirementExtraction(_Strict):
    requirements: list[Claim]  # only needs the source explicitly states
    budget: Claim | None
    deadline: Claim | None


class EvidenceEvaluation(_Strict):
    verdict: Literal["supported", "partially_supported", "unsupported"]
    reliability: float  # 0..1
    issues: list[str]
    supporting: list[Claim]


class ResearchBrief(_Strict):
    findings: list[Claim]
    open_questions: list[str]  # things the evidence does NOT answer


class CriterionAssessment(_Strict):
    name: str
    score: float  # 0..100
    rationale: str
    support: Claim


class QualificationAssessment(_Strict):
    fit_score: float  # 0..100
    criteria: list[CriterionAssessment]
    risks: list[str]


class OutreachDraftAI(_Strict):
    subject: str
    body: str
    claims: list[Claim]  # every factual statement about the recipient/opportunity in `body`


class OpportunityDossier(_Strict):
    """One call that replaces analysis + extraction + evidence evaluation + research + fit
    assessment, so later agents reuse it instead of re-reading the same source."""

    summary: str
    requirements: list[Claim]  # only needs the source explicitly states
    budget: Claim | None
    deadline: Claim | None
    ai_project: Literal["explicit", "implied", "none"]  # does the source describe AI work?
    ai_project_support: Claim | None  # required quote when ai_project == "explicit"
    verdict: Literal["supported", "partially_supported", "unsupported"]
    reliability: float  # 0..1
    findings: list[Claim]  # useful, source-backed facts about the buyer/partner
    open_questions: list[str]  # what the evidence does NOT answer
    fit_score: float  # 0..100 fit for a paid AI implementation team
    risks: list[str]
    external_delivery: Literal["permitted", "not_permitted", "unclear"]  # may outsiders deliver?
    small_team_fit: Literal["good", "stretch", "poor", "unclear"]  # realistic for a small team?
    suggested_next_action: str  # one concrete step, grounded in the evidence
