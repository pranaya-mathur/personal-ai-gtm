"""automatic source discovery candidates and structured contact routes

Revision ID: 0007
Revises: 0006
"""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("opportunities", sa.Column("channel", sa.String(16), nullable=False, server_default="direct_buyer"))
    op.execute("UPDATE opportunities SET channel = 'agency' WHERE organization_id IN (SELECT id FROM organizations WHERE kind = 'agency')")
    op.create_table(
        "source_candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("domain", sa.String(253), nullable=False),
        sa.Column("name", sa.String(256)),
        sa.Column("country", sa.String(64)),
        sa.Column("origin", sa.String(48), nullable=False),
        sa.Column("origin_ref", sa.String(2048)),
        sa.Column("page_url", sa.String(2048)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("status_detail", sa.String(256)),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column("signals", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("domain", name="uq_source_candidates_domain"),
        sa.CheckConstraint(
            "status IN ('new', 'signal', 'no_signal', 'blocked', 'error')",
            name="ck_source_candidates_status",
        ),
    )
    op.create_table(
        "contact_routes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("opportunity_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid()),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("value", sa.String(2048), nullable=False),
        sa.Column("role", sa.String(256)),
        sa.Column("source_url", sa.String(2048), nullable=False),
        sa.Column("snippet", sa.Text()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.UniqueConstraint("opportunity_id", "kind", "value", name="uq_contact_routes_value"),
        sa.CheckConstraint(
            "kind IN ('email', 'inquiry_form', 'procurement_link', 'page')",
            name="ck_contact_routes_kind",
        ),
    )
    op.create_index("ix_contact_routes_org", "contact_routes", ["organization_id"])


def downgrade() -> None:
    op.drop_column("opportunities", "channel")
    op.drop_index("ix_contact_routes_org", table_name="contact_routes")
    op.drop_table("contact_routes")
    op.drop_table("source_candidates")
