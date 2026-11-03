"""record who verified an opportunity

Revision ID: 0006
Revises: 0005
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("opportunities", sa.Column("verified_by", sa.String(128)))
    op.add_column("opportunities", sa.Column("verified_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("opportunities", "verified_at")
    op.drop_column("opportunities", "verified_by")
