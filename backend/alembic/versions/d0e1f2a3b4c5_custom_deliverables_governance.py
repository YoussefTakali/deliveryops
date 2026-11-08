"""livrables catalogue + per-project livrables, manual (external) team members, AI governance + usage ledger

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd0e1f2a3b4c5'
down_revision: Union[str, Sequence[str], None] = 'c9d0e1f2a3b4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('custom_deliverables',
        sa.Column('id', sa.String(40), primary_key=True),
        sa.Column('owner', sa.String(200), nullable=False, index=True),
        sa.Column('project_id', sa.String(40), sa.ForeignKey('delivery_projects.id', ondelete='CASCADE'), nullable=True, index=True),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('output', sa.String(10), nullable=False, server_default='docx'),
        sa.Column('inputs', sa.JSON(), nullable=True),
        sa.Column('sections', sa.JSON(), nullable=True),
        sa.Column('instructions', sa.Text(), nullable=False, server_default=''),
        sa.Column('examples', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
    op.create_table('project_livrables',
        sa.Column('id', sa.String(40), primary_key=True),
        sa.Column('project_id', sa.String(40), sa.ForeignKey('delivery_projects.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('key', sa.String(60), nullable=False),
        sa.Column('added_by', sa.String(200), nullable=False, server_default=''),
        sa.Column('added_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('project_id', 'key'))
    op.create_table('external_people',
        sa.Column('id', sa.String(40), primary_key=True),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('email', sa.String(200), nullable=False, server_default=''),
        sa.Column('profile', sa.String(60), nullable=False, server_default='confirmed'),
        sa.Column('weekly_capacity_h', sa.Float(), nullable=False, server_default='35'),
        sa.Column('created_by', sa.String(200), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True))
    op.create_table('governance_policy',
        sa.Column('id', sa.String(20), primary_key=True),
        sa.Column('ai_enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('default_daily_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('agent_caps', sa.JSON(), nullable=True),
        sa.Column('updated_by', sa.String(200), nullable=False, server_default=''),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
    op.create_table('user_governance',
        sa.Column('username', sa.String(200), primary_key=True),
        sa.Column('app_blocked', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('ai_blocked', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('daily_token_quota', sa.Integer(), nullable=True),
        sa.Column('note', sa.String(300), nullable=False, server_default=''),
        sa.Column('updated_by', sa.String(200), nullable=False, server_default=''),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
    op.create_table('usage_ledger',
        sa.Column('id', sa.String(40), primary_key=True),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=True, index=True),
        sa.Column('username', sa.String(200), nullable=False, server_default='', index=True),
        sa.Column('agent', sa.String(60), nullable=False, server_default='', index=True),
        sa.Column('purpose', sa.String(200), nullable=False, server_default=''),
        sa.Column('model', sa.String(60), nullable=False, server_default=''),
        sa.Column('tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('status', sa.String(20), nullable=False, server_default='ok'))


def downgrade() -> None:
    for t in ('usage_ledger', 'user_governance', 'governance_policy', 'external_people', 'project_livrables', 'custom_deliverables'):
        op.drop_table(t)
