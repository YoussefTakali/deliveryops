"""livrable scopes: project-level vs per-périmètre livrables

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, Sequence[str], None] = 'd0e1f2a3b4c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'livrable_scopes',
        sa.Column('id', sa.String(40), primary_key=True),
        sa.Column('project_id', sa.String(40), sa.ForeignKey('delivery_projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('key', sa.String(60), nullable=False),
        sa.Column('level', sa.String(12), nullable=False, server_default='project'),
        sa.Column('perimeter_ids', sa.JSON(), nullable=True),
        sa.Column('updated_by', sa.String(200), nullable=False, server_default=''),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('project_id', 'key'),
    )
    op.create_index('ix_livrable_scopes_project_id', 'livrable_scopes', ['project_id'])


def downgrade() -> None:
    op.drop_index('ix_livrable_scopes_project_id', table_name='livrable_scopes')
    op.drop_table('livrable_scopes')
