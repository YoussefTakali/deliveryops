"""competence matrix (user_skills), member↔user link, generated deliverables per périmètre

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, Sequence[str], None] = 'f6a7b8c9d0e1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('user_skills',
        sa.Column('id', sa.String(40), primary_key=True),
        sa.Column('owner', sa.String(200), nullable=False, index=True),
        sa.Column('skill', sa.String(80), nullable=False, index=True),
        sa.Column('level', sa.Integer(), nullable=False, server_default='3'),
        sa.Column('category', sa.String(30), nullable=False, server_default='technology'),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
    op.create_table('team_member_users',
        sa.Column('member_id', sa.String(40), sa.ForeignKey('team_members.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('owner', sa.String(200), nullable=False, index=True))
    op.create_table('perimeter_deliverables',
        sa.Column('id', sa.String(40), primary_key=True),
        sa.Column('perimeter_id', sa.String(40), sa.ForeignKey('perimeters.id'), nullable=False, index=True),
        sa.Column('key', sa.String(40), nullable=False, index=True),
        sa.Column('title', sa.String(200), nullable=False, server_default=''),
        sa.Column('filename', sa.String(300), nullable=False, server_default=''),
        sa.Column('file_id', sa.String(40), nullable=True),
        sa.Column('draft', sa.JSON(), nullable=False),
        sa.Column('created_by', sa.String(200), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_table('perimeter_deliverables')
    op.drop_table('team_member_users')
    op.drop_table('user_skills')
