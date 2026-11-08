"""nullable notification perimeter (for global/admin-scoped notifications)

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-09-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column('notifications', 'perimeter_id', existing_type=sa.String(length=40), nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column('notifications', 'perimeter_id', existing_type=sa.String(length=40), nullable=False)
