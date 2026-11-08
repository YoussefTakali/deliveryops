"""agent skills

Revision ID: a1b2c3d4e5f6
Revises: 67ab9b866b54
Create Date: 2026-09-17 17:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '67ab9b866b54'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'agent_skills',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('agent', sa.String(length=40), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('source', sa.String(length=30), nullable=False),
        sa.Column('perimeter_id', sa.String(length=40), nullable=True),
        sa.Column('created_by', sa.String(length=200), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_agent_skills_agent'), 'agent_skills', ['agent'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_agent_skills_agent'), table_name='agent_skills')
    op.drop_table('agent_skills')
