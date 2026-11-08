"""ui translations: org-wide cache of machine-translated interface strings

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a3b4c5d6e7f8'
down_revision: Union[str, Sequence[str], None] = 'f2a3b4c5d6e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ui_translations',
        sa.Column('id', sa.String(40), primary_key=True),
        sa.Column('lang', sa.String(20), nullable=False),
        sa.Column('source_hash', sa.String(64), nullable=False),
        sa.Column('source', sa.Text(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('lang', 'source_hash'),
    )
    op.create_index('ix_ui_translations_lang', 'ui_translations', ['lang'])


def downgrade() -> None:
    op.drop_index('ix_ui_translations_lang', table_name='ui_translations')
    op.drop_table('ui_translations')
