"""project-level deliverables (plan de charge spans the whole project)

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c9d0e1f2a3b4'
down_revision: Union[str, Sequence[str], None] = 'b8c9d0e1f2a3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('project_deliverables',
        sa.Column('id', sa.String(40), primary_key=True),
        sa.Column('project_id', sa.String(40), sa.ForeignKey('delivery_projects.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('key', sa.String(40), nullable=False, index=True),
        sa.Column('title', sa.String(200), nullable=False, server_default=''),
        sa.Column('filename', sa.String(300), nullable=False, server_default=''),
        sa.Column('file_id', sa.String(40), nullable=True),
        sa.Column('draft', sa.JSON(), nullable=True),
        sa.Column('created_by', sa.String(200), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_table('project_deliverables')
