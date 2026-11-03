"""search profiles and source check

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('search_profiles',
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('keywords', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('regions', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('connector_names', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('max_results', sa.Integer(), nullable=False),
    sa.Column('max_age_days', sa.Integer(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('max_results >= 1 AND max_results <= 500', name='ck_profiles_max_results'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name', name='uq_search_profiles_name')
    )
    op.add_column(
        'evidence',
        sa.Column('source_check_status', sa.String(length=24), nullable=False,
                  server_default='not_checked'),
    )
    op.create_check_constraint(
        'ck_evidence_source_check_status', 'evidence',
        "source_check_status IN ('not_checked', 'confirmed', 'mismatch', 'unreachable', 'blocked')",
    )
    op.add_column('evidence', sa.Column('source_checked_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_constraint('ck_evidence_source_check_status', 'evidence', type_='check')
    op.drop_column('evidence', 'source_checked_at')
    op.drop_column('evidence', 'source_check_status')
    op.drop_table('search_profiles')
