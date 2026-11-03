"""capability profile, proposals, onboarding checklist

Revision ID: 0009
Revises: 0008
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _ts() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "capability_profile",
        sa.Column("id", sa.Uuid(), nullable=False),
        *_ts(),
        sa.Column("areas", JSON, nullable=False),
        sa.Column("updated_by", sa.String(128)),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "proposals",
        sa.Column("id", sa.Uuid(), nullable=False),
        *_ts(),
        sa.Column("opportunity_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("scope", JSON, nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("price_text", sa.String(256)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("prepared_by", sa.String(128), nullable=False),
        sa.Column("approved_by", sa.String(128)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunities.id"], ondelete="CASCADE"),
        sa.CheckConstraint("status IN ('draft', 'approved', 'sent', 'accepted', 'declined')", name="ck_proposals_status"),
        sa.CheckConstraint("status NOT IN ('approved', 'sent', 'accepted') OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)", name="ck_proposals_requires_approval"),
    )
    op.create_table(
        "onboarding_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        *_ts(),
        sa.Column("deal_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(300), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("done", sa.Boolean(), nullable=False),
        sa.Column("done_by", sa.String(128)),
        sa.Column("done_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["deal_id"], ["deals.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("deal_id", "label", name="uq_onboarding_label"),
    )


def downgrade() -> None:
    op.drop_table("onboarding_items")
    op.drop_table("proposals")
    op.drop_table("capability_profile")
