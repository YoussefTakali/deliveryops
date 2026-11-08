"""microsoft users

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-17 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'microsoft_users',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('email', sa.String(length=200), nullable=False),
        sa.Column('subject', sa.String(length=200), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('role_id', sa.String(length=40), nullable=False),
        sa.Column('clients', sa.JSON(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['role_id'], ['roles.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_microsoft_users_email'), 'microsoft_users', ['email'], unique=True)
    op.create_index(op.f('ix_microsoft_users_role_id'), 'microsoft_users', ['role_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_microsoft_users_role_id'), table_name='microsoft_users')
    op.drop_index(op.f('ix_microsoft_users_email'), table_name='microsoft_users')
    op.drop_table('microsoft_users')
