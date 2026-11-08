"""llm provider configs: per-agent endpoint / API key / model edited from the admin UI

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, Sequence[str], None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'llm_provider_configs',
        sa.Column('key', sa.String(60), primary_key=True),
        sa.Column('base_url', sa.String(300), nullable=False, server_default=''),
        sa.Column('api_key_enc', sa.Text(), nullable=False, server_default=''),
        sa.Column('key_hint', sa.String(40), nullable=False, server_default=''),
        sa.Column('model', sa.String(200), nullable=False, server_default=''),
        sa.Column('updated_by', sa.String(200), nullable=False, server_default=''),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('llm_provider_configs')
