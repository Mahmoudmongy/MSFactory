"""production: work orders and their materials

Revision ID: c1a2b3d4e5f6
Revises: 627e2e1c6b80
Create Date: 2026-11-18 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1a2b3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '627e2e1c6b80'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'work_orders',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('number', sa.String(length=40), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('item_id', sa.Integer(), nullable=False),
        sa.Column('variant_id', sa.Integer(), nullable=True),
        sa.Column('warehouse_id', sa.Integer(), nullable=False),
        sa.Column('quantity', sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column('produced_quantity', sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('due_date', sa.Date(), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('notes', sa.Text(), nullable=False),
        sa.Column('material_cost', sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column('unit_cost', sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.ForeignKeyConstraint(['item_id'], ['items.id']),
        sa.ForeignKeyConstraint(['variant_id'], ['item_variants.id']),
        sa.ForeignKeyConstraint(['warehouse_id'], ['warehouses.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('company_id', 'number', name='uq_work_orders_company_number'),
    )
    op.create_index(op.f('ix_work_orders_item_id'), 'work_orders', ['item_id'], unique=False)
    op.create_index(op.f('ix_work_orders_number'), 'work_orders', ['number'], unique=False)
    op.create_index(op.f('ix_work_orders_start_date'), 'work_orders', ['start_date'], unique=False)
    op.create_index(op.f('ix_work_orders_warehouse_id'), 'work_orders', ['warehouse_id'], unique=False)

    op.create_table(
        'work_order_materials',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('work_order_id', sa.Integer(), nullable=False),
        sa.Column('item_id', sa.Integer(), nullable=False),
        sa.Column('variant_id', sa.Integer(), nullable=True),
        sa.Column('quantity', sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column('cost_amount', sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column('notes', sa.Text(), nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id']),
        sa.ForeignKeyConstraint(['item_id'], ['items.id']),
        sa.ForeignKeyConstraint(['variant_id'], ['item_variants.id']),
        sa.ForeignKeyConstraint(['work_order_id'], ['work_orders.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_work_order_materials_item_id'), 'work_order_materials', ['item_id'], unique=False)
    op.create_index(op.f('ix_work_order_materials_work_order_id'), 'work_order_materials', ['work_order_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_work_order_materials_work_order_id'), table_name='work_order_materials')
    op.drop_index(op.f('ix_work_order_materials_item_id'), table_name='work_order_materials')
    op.drop_table('work_order_materials')
    op.drop_index(op.f('ix_work_orders_warehouse_id'), table_name='work_orders')
    op.drop_index(op.f('ix_work_orders_start_date'), table_name='work_orders')
    op.drop_index(op.f('ix_work_orders_number'), table_name='work_orders')
    op.drop_index(op.f('ix_work_orders_item_id'), table_name='work_orders')
    op.drop_table('work_orders')
