from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import LlmUsage, Opportunity, OutreachDraft, ProviderSpend


class PipelineAnalyticsAgent:
    def __init__(self, session: Session) -> None:
        self.session = session

    def run(self) -> dict[str, object]:
        by_category = dict(
            self.session.execute(
                select(Opportunity.category, func.count()).group_by(Opportunity.category)
            ).all()
        )
        by_status = dict(
            self.session.execute(
                select(Opportunity.status, func.count()).group_by(Opportunity.status)
            ).all()
        )
        pending = self.session.execute(
            select(func.count())
            .select_from(OutreachDraft)
            .where(OutreachDraft.status == "pending_approval")
        ).scalar_one()
        spend = self.session.execute(
            select(func.coalesce(func.sum(ProviderSpend.amount_usd), 0))
        ).scalar_one()
        tok_in, tok_out, llm_cost = self.session.execute(
            select(
                func.coalesce(func.sum(LlmUsage.input_tokens), 0),
                func.coalesce(func.sum(LlmUsage.output_tokens), 0),
                func.coalesce(func.sum(LlmUsage.cost_usd), 0),
            )
        ).one()
        return {
            "llm_input_tokens": int(tok_in),
            "llm_output_tokens": int(tok_out),
            "llm_cost_usd": float(llm_cost),
            "opportunities_by_category": by_category,
            "opportunities_by_status": by_status,
            "drafts_pending_approval": pending,
            "total_provider_spend_usd": float(spend),
        }
