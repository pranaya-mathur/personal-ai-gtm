"""allow cancelled workflow runs

Revision ID: 0004
Revises: 0003
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

OLD = "status IN ('queued', 'running', 'succeeded', 'failed')"
NEW = "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')"


def upgrade() -> None:
    op.drop_constraint("ck_workflow_runs_status", "workflow_runs", type_="check")
    op.create_check_constraint("ck_workflow_runs_status", "workflow_runs", NEW)


def downgrade() -> None:
    op.execute("UPDATE workflow_runs SET status = 'failed' WHERE status = 'cancelled'")
    op.drop_constraint("ck_workflow_runs_status", "workflow_runs", type_="check")
    op.create_check_constraint("ck_workflow_runs_status", "workflow_runs", OLD)
