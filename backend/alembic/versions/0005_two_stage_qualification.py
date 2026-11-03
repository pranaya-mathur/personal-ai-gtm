"""two-stage qualification fields, ai cache key, llm usage agent

Revision ID: 0005
Revises: 0004
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column("opportunities", sa.Column("source_status", sa.String(16), nullable=False, server_default="unknown"))
    op.add_column("opportunities", sa.Column("deadline", sa.DateTime(timezone=True)))
    op.add_column("opportunities", sa.Column("budget_text", sa.String(256)))
    op.add_column("opportunities", sa.Column("contact_route", sa.String(2048)))
    op.add_column("opportunities", sa.Column("technical_relevance", sa.String(16), nullable=False, server_default="unassessed"))
    op.add_column("opportunities", sa.Column("triage", sa.String(24), nullable=False, server_default="unassessed"))
    op.add_column("opportunities", sa.Column("rejection_reason", sa.String(64)))
    op.add_column("opportunities", sa.Column("assessment", JSON, nullable=False, server_default="{}"))
    op.create_check_constraint("ck_opps_triage", "opportunities", "triage IN ('unassessed', 'actionable', 'needs_verification', 'rejected')")
    op.create_check_constraint("ck_opps_tech_relevance", "opportunities", "technical_relevance IN ('unassessed', 'relevant', 'unclear', 'not_relevant')")
    op.create_check_constraint("ck_opps_source_status", "opportunities", "source_status IN ('unknown', 'open', 'closed', 'awarded')")
    op.add_column("llm_usage", sa.Column("agent", sa.String(48)))
    op.add_column("llm_usage", sa.Column("reason", sa.Text()))
    op.add_column("ai_analyses", sa.Column("input_hash", sa.String(64)))
    op.create_index("ix_ai_analyses_input_hash", "ai_analyses", ["input_hash"])


def downgrade() -> None:
    op.drop_index("ix_ai_analyses_input_hash", table_name="ai_analyses")
    op.drop_column("ai_analyses", "input_hash")
    op.drop_column("llm_usage", "reason")
    op.drop_column("llm_usage", "agent")
    for name in ("ck_opps_source_status", "ck_opps_tech_relevance", "ck_opps_triage"):
        op.drop_constraint(name, "opportunities", type_="check")
    for col in ("assessment", "rejection_reason", "triage", "technical_relevance", "contact_route", "budget_text", "deadline", "source_status"):
        op.drop_column("opportunities", col)
