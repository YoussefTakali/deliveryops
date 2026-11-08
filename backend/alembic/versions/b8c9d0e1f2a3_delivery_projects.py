"""delivery projects (a manager runs one or more), project team, perimeter.project_id

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b8c9d0e1f2a3'
down_revision: Union[str, Sequence[str], None] = 'a7b8c9d0e1f2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('delivery_projects',
        sa.Column('id', sa.String(40), primary_key=True),
        sa.Column('client_id', sa.String(40), sa.ForeignKey('clients.id'), nullable=False, index=True),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('manager', sa.String(200), nullable=False, server_default='', index=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='active', index=True),
        sa.Column('technologies', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True))
    op.create_table('project_members',
        sa.Column('id', sa.String(40), primary_key=True),
        sa.Column('project_id', sa.String(40), sa.ForeignKey('delivery_projects.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('owner', sa.String(200), nullable=False, index=True),
        sa.Column('role_in_project', sa.String(60), nullable=False, server_default='member'),
        sa.Column('allocation_pct', sa.Integer(), nullable=False, server_default='100'),
        sa.Column('added_by', sa.String(200), nullable=False, server_default=''),
        sa.Column('added_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('perimeters', sa.Column('project_id', sa.String(40), nullable=True))
    op.create_index('ix_perimeters_project_id', 'perimeters', ['project_id'])
    op.create_foreign_key('fk_perimeters_project_id', 'perimeters', 'delivery_projects', ['project_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint('fk_perimeters_project_id', 'perimeters', type_='foreignkey')
    op.drop_index('ix_perimeters_project_id', 'perimeters')
    op.drop_column('perimeters', 'project_id')
    op.drop_table('project_members')
    op.drop_table('delivery_projects')
