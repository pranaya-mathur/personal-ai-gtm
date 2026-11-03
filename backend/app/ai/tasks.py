import uuid
from functools import partial

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import schemas as S
from app.ai.claims import AMBIGUOUS, support_report, validate_output
from app.ai.gateway import LLMGateway
from app.ai.prompts import SYSTEM_BASE, EvidenceItem, build_user_prompt
from app.llm.base import T
from app.models import Evidence, Opportunity, OpportunityEvidence, Organization


class AITasks:
    """The six production AI capabilities. Each is a strict-schema call whose claims must be
    quoted verbatim from this opportunity's stored evidence, else the output is rejected."""

    def __init__(self, session: Session, gateway: LLMGateway) -> None:
        self.session, self.gateway = session, gateway

    def evidence_for(self, opp: Opportunity) -> list[EvidenceItem]:
        rows = self.session.execute(
            select(Evidence)
            .join(OpportunityEvidence, OpportunityEvidence.evidence_id == Evidence.id)
            .where(OpportunityEvidence.opportunity_id == opp.id)
            .order_by(Evidence.retrieved_at)
        ).scalars()
        return [EvidenceItem(str(e.id), e.source_url, e.original_text) for e in rows]

    def _header(self, opp: Opportunity, recipient: str | None = None) -> str:
        org = self.session.get(Organization, opp.organization_id) if opp.organization_id else None
        lines = [
            f"Opportunity title: {opp.title}",
            f"Evidence class: {opp.category}",
            f"Organization: {org.name if org else 'unknown'}",
        ]
        if recipient:
            lines.append(f"Recipient name (from source): {recipient}")
        return "\n".join(lines)

    def _run(
        self,
        task: str,
        schema: type[T],
        opp: Opportunity,
        run_id: uuid.UUID | None,
        min_claims: int,
        recipient: str | None = None,
        agent: str = "research_evidence",
    ) -> T | None:
        items = self.evidence_for(opp)
        if not items:
            return None
        validator = partial(
            validate_output,
            evidence={e.id: e.text for e in items},
            min_claims=min_claims,
            evidence_urls={e.url for e in items},
        )
        return self.gateway.run_task(
            task=task,
            schema=schema,
            system=SYSTEM_BASE,
            user=build_user_prompt(task, self._header(opp, recipient), items),
            run_id=run_id,
            opportunity_id=opp.id,
            validator=validator,
            agent=agent,
        )

    def dossier(self, opp: Opportunity, run_id: uuid.UUID | None) -> S.OpportunityDossier | None:
        d = self._run("dossier", S.OpportunityDossier, opp, run_id, 1)
        return self._downgrade_unsupported(d, opp, run_id) if d is not None else None

    def _downgrade_unsupported(
        self, d: S.OpportunityDossier, opp: Opportunity, run_id: uuid.UUID | None
    ) -> S.OpportunityDossier:
        """Claims whose quote only ambiguously backs them never keep their full weight: the
        dossier is downgraded (explicit->implied, supported->partially_supported) and the
        deterministic qualification never relies on them. An optional, separately authorised model
        call may clear claims the deterministic check could not decide - ambiguous ones only."""
        items = self.evidence_for(opp)
        report = support_report(d, {e.id: e.text for e in items})
        doubtful = [(c, why) for c, label, why in report if label == AMBIGUOUS]
        if doubtful and self.gateway.settings.ai_semantic_check_enabled:
            doubtful = self._judge(opp, run_id, doubtful, items)
        if not doubtful:
            return d
        update: dict[str, object] = {}
        support = d.ai_project_support
        if d.ai_project == "explicit" and (
            support is None or any(c.quote == support.quote for c, _ in doubtful)
        ):
            update["ai_project"] = "implied"
        if d.verdict == "supported":
            update["verdict"] = "partially_supported"
        return d.model_copy(update=update) if update else d

    def _judge(
        self,
        opp: Opportunity,
        run_id: uuid.UUID | None,
        doubtful: list[tuple[S.Claim, str]],
        items: list[EvidenceItem],
    ) -> list[tuple[S.Claim, str]]:
        lines = [f"CLAIM {i}: {c.text}\nQUOTE {i}: {c.quote}" for i, (c, _) in enumerate(doubtful)]
        wanted = {c.evidence_id for c, _ in doubtful}
        res = self.gateway.run_task(
            task="claim_support_check",
            schema=S.ClaimJudgements,
            system=SYSTEM_BASE,
            user=build_user_prompt(
                "claim_support_check", "\n".join(lines), [e for e in items if e.id in wanted]
            ),
            run_id=run_id,
            opportunity_id=opp.id,
            validator=lambda _o: None,
            agent="research_evidence",
        )
        if res is None:
            return doubtful  # no verdict: stay downgraded
        cleared = {j.index for j in res.results if j.supports}
        return [d for i, d in enumerate(doubtful) if i not in cleared]

    def analyze(self, opp: Opportunity, run_id: uuid.UUID | None) -> S.OpportunityAnalysis | None:
        return self._run("opportunity_analysis", S.OpportunityAnalysis, opp, run_id, 1)

    def extract_requirements(
        self, opp: Opportunity, run_id: uuid.UUID | None
    ) -> S.RequirementExtraction | None:
        return self._run("requirement_extraction", S.RequirementExtraction, opp, run_id, 0)

    def evaluate_evidence(
        self, opp: Opportunity, run_id: uuid.UUID | None
    ) -> S.EvidenceEvaluation | None:
        return self._run("evidence_evaluation", S.EvidenceEvaluation, opp, run_id, 0)

    def research(self, opp: Opportunity, run_id: uuid.UUID | None) -> S.ResearchBrief | None:
        return self._run("research_brief", S.ResearchBrief, opp, run_id, 1)

    def assess(
        self, opp: Opportunity, run_id: uuid.UUID | None
    ) -> S.QualificationAssessment | None:
        return self._run("qualification", S.QualificationAssessment, opp, run_id, 1)

    def draft(
        self, opp: Opportunity, run_id: uuid.UUID | None, recipient: str | None
    ) -> S.OutreachDraftAI | None:
        return self._run(
            "outreach_draft", S.OutreachDraftAI, opp, run_id, 1, recipient, "outreach_intelligence"
        )
