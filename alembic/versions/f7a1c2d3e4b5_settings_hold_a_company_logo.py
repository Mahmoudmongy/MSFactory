"""settings: hold a company logo

A logo travels as a base64 data URL, which does not fit in the 500-character
value column the other settings use. Widening the column keeps the logo in the
same key/value table as the rest of the company profile instead of adding a
second storage path.

Revision ID: f7a1c2d3e4b5
Revises: d1e2f3a4b5c6
Create Date: 2026-11-19 10:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7a1c2d3e4b5'
down_revision: Union[str, Sequence[str], None] = 'd1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        'company_settings',
        'value',
        existing_type=sa.String(length=500),
        type_=sa.Text(),
        existing_nullable=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        'company_settings',
        'value',
        existing_type=sa.Text(),
        type_=sa.String(length=500),
        existing_nullable=False,
    )
