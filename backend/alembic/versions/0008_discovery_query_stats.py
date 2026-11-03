"""discovery query effectiveness ledger

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "discovery_query_stats",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fingerprint", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("query_text", sa.String(512), nullable=False),
        sa.Column("searches", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("urls_retrieved", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pages_ok", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pages_blocked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confirmed_active", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("agency_partner_need", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("needs_verification", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("false_positives", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duplicates", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(12, 4), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fingerprint", "provider", name="uq_discovery_query_stats_fp_provider"),
    )
    op.create_index("ix_discovery_query_stats_channel", "discovery_query_stats", ["channel"])


def downgrade() -> None:
    op.drop_index("ix_discovery_query_stats_channel", table_name="discovery_query_stats")
    op.drop_table("discovery_query_stats")
