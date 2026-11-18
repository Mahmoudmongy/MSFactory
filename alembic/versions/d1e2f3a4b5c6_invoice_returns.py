"""invoices: link a return back to the invoice it reverses

Revision ID: d1e2f3a4b5c6
Revises: c1a2b3d4e5f6
Create Date: 2026-11-18 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, Sequence[str], None] = 'c1a2b3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('invoices', sa.Column('return_of_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_invoices_return_of_id_invoices', 'invoices', 'invoices', ['return_of_id'], ['id']
    )
    op.create_index('ix_invoices_return_of_id', 'invoices', ['return_of_id'])
    op.add_column('invoice_lines', sa.Column('source_line_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_invoice_lines_source_line_id', 'invoice_lines', 'invoice_lines', ['source_line_id'], ['id']
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_invoice_lines_source_line_id', 'invoice_lines', type_='foreignkey')
    op.drop_column('invoice_lines', 'source_line_id')
    op.drop_index('ix_invoices_return_of_id', table_name='invoices')
    op.drop_constraint('fk_invoices_return_of_id_invoices', 'invoices', type_='foreignkey')
    op.drop_column('invoices', 'return_of_id')
