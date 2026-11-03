"""Offline, free, deterministic LLM. Produces schema-valid, evidence-grounded outputs by quoting
the evidence blocks found in the prompt, so the full AI pipeline runs without any paid call."""

import re

from app.ai import schemas as S
from app.llm.base import StructuredResult, T

_BLOCK = re.compile(r'<evidence id="([^"]+)" url="[^"]*">\n(.*?)\n</evidence>', re.S)


def _first_quote(text: str) -> str:
    line = next((ln.strip() for ln in text.splitlines() if len(ln.strip()) >= 8), text.strip())
    return line[:120]


class MockLLM:
    provider = "mock"
    model = "mock"
    is_mock = True

    def ensure_ready(self) -> None:
        return None

    def complete_structured(
        self, system: str, user: str, schema: type[T], max_output_tokens: int
    ) -> StructuredResult:
        blocks = _BLOCK.findall(user)
        if not blocks:
            raise ValueError("MockLLM needs at least one <evidence> block in the prompt")
        eid, text = blocks[0]
        claim = S.Claim(
            text="Source states: " + _first_quote(text), evidence_id=eid, quote=_first_quote(text)
        )
        out: object
        if schema is S.OpportunityAnalysis:
            out = S.OpportunityAnalysis(summary="[MOCK] " + _first_quote(text), claims=[claim])
        elif schema is S.RequirementExtraction:
            out = S.RequirementExtraction(requirements=[claim], budget=None, deadline=None)
        elif schema is S.EvidenceEvaluation:
            out = S.EvidenceEvaluation(
                verdict="supported", reliability=0.7, issues=[], supporting=[claim]
            )
        elif schema is S.ResearchBrief:
            out = S.ResearchBrief(
                findings=[claim], open_questions=["Budget and timeline are not stated."]
            )
        elif schema is S.QualificationAssessment:
            out = S.QualificationAssessment(
                fit_score=70.0,
                criteria=[
                    S.CriterionAssessment(
                        name="stated_need", score=70.0, rationale="[MOCK]", support=claim
                    )
                ],
                risks=["[MOCK] unverified beyond the cited source"],
            )
        elif schema is S.OutreachDraftAI:
            out = S.OutreachDraftAI(
                subject="Regarding your AI project",
                body=(
                    'Hi,\n\nI read: "' + claim.quote + '".\n\n'
                    "[YOUR RELEVANT EXPERIENCE]\n\n[PROPOSED NEXT STEP]\n"
                ),
                claims=[claim],
            )
        else:
            raise ValueError(f"MockLLM has no generator for {schema.__name__}")
        n_in = max(1, (len(system) + len(user)) // 4)
        return StructuredResult(
            parsed=out, provider="mock", model="mock", input_tokens=n_in, output_tokens=120
        )
