"""imports: mapping table and job log for Manager.io migration

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-11-20 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c4d5e6f7a8b9'
down_revision: Union[str, Sequence[str], None] = 'b3c4d5e6f7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'import_jobs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('business_key', sa.String(length=120), nullable=False),
        sa.Column('business_name', sa.String(length=200), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('dry_run', sa.Boolean(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('report', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('error', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name='fk_import_jobs_company_id_companies'),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name='fk_import_jobs_created_by_id_users'),
        sa.PrimaryKeyConstraint('id', name='pk_import_jobs'),
    )
    op.create_index('ix_import_jobs_company_id', 'import_jobs', ['company_id'])

    op.create_table(
        'import_mappings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('entity', sa.String(length=20), nullable=False),
        sa.Column('external_key', sa.String(length=120), nullable=False),
        sa.Column('local_id', sa.Integer(), nullable=False),
        sa.Column('label', sa.String(length=200), nullable=False),
        sa.Column('job_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name='fk_import_mappings_company_id_companies'),
        sa.ForeignKeyConstraint(['job_id'], ['import_jobs.id'], name='fk_import_mappings_job_id_import_jobs'),
        sa.PrimaryKeyConstraint('id', name='pk_import_mappings'),
        sa.UniqueConstraint('company_id', 'source', 'entity', 'external_key', name='uq_import_mappings_identity'),
    )
    op.create_index('ix_import_mappings_company_id', 'import_mappings', ['company_id'])
    op.create_index('ix_import_mappings_lookup', 'import_mappings', ['company_id', 'source', 'entity'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_import_mappings_lookup', table_name='import_mappings')
    op.drop_index('ix_import_mappings_company_id', table_name='import_mappings')
    op.drop_table('import_mappings')
    op.drop_index('ix_import_jobs_company_id', table_name='import_jobs')
    op.drop_table('import_jobs')
