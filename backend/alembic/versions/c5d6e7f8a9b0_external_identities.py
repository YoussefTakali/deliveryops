"""external identities: Microsoft / Google sign-in linked to native accounts

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c5d6e7f8a9b0'
down_revision: Union[str, Sequence[str], None] = 'b4c5d6e7f8a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'external_identities',
        sa.Column('id', sa.String(40), primary_key=True),
        sa.Column('user_id', sa.String(40), sa.ForeignKey('auth_users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('provider', sa.String(20), nullable=False),
        sa.Column('subject', sa.String(200), nullable=False),
        sa.Column('email', sa.String(200), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('provider', 'subject', name='uq_external_identity'),
    )
    op.create_index('ix_external_identities_user_id', 'external_identities', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_external_identities_user_id', table_name='external_identities')
    op.drop_table('external_identities')
