"""run leases + truthful outcomes, durable provider runs, source schedules

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def _ts() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.add_column("workflow_runs", sa.Column("lease_owner", sa.String(96)))
    op.add_column("workflow_runs", sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
    op.add_column("workflow_runs", sa.Column("outcome", sa.String(32)))
    op.add_column(
        "workflow_runs",
        sa.Column("trigger", sa.String(16), nullable=False, server_default="manual"),
    )
    op.create_table(
        "provider_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        *_ts(),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(96), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(), sa.ForeignKey("workflow_runs.id")),
        sa.Column("spend_id", sa.Uuid(), sa.ForeignKey("provider_spend.id")),
        sa.Column("external_id", sa.String(64)),
        sa.Column("dataset_id", sa.String(64)),
        sa.Column("status", sa.String(16), nullable=False, server_default="submitting"),
        sa.Column("max_charge_usd", sa.Numeric(12, 4), nullable=False, server_default="0"),
        sa.Column("reported_usd", sa.Numeric(12, 4)),
        sa.Column("event_usd", sa.Numeric(12, 4)),
        sa.Column("charged_usd", sa.Numeric(12, 4)),
        sa.Column("cost_basis", sa.String(24), nullable=False, server_default="estimate"),
        sa.Column("detail", JSON, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("finalized_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_provider_runs_idempotency_key"),
    )
    op.create_index("ix_provider_runs_spend", "provider_runs", ["spend_id"])
    op.create_index("ix_provider_runs_external", "provider_runs", ["external_id"])
    op.create_table(
        "source_schedules",
        sa.Column("id", sa.Uuid(), nullable=False),
        *_ts(),
        sa.Column("connector", sa.String(64), nullable=False),
        sa.Column("channel", sa.String(24), nullable=False),
        sa.Column("interval_minutes", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("paid", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("min_gap_minutes", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("last_status", sa.String(24)),
        sa.Column("last_error", sa.String(300)),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_retrieved", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_new", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("connector", name="uq_source_schedules_connector"),
    )


def downgrade() -> None:
    op.drop_table("source_schedules")
    op.drop_index("ix_provider_runs_external", table_name="provider_runs")
    op.drop_index("ix_provider_runs_spend", table_name="provider_runs")
    op.drop_table("provider_runs")
    for c in ("trigger", "outcome", "lease_expires_at", "lease_owner"):
        op.drop_column("workflow_runs", c)
