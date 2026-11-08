"""work companion: profiles, memories, activity, suggestions, messages, org policy

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-18 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'companion_profiles',
        sa.Column('owner', sa.String(length=200), nullable=False),
        sa.Column('display_name', sa.String(length=200), nullable=False),
        sa.Column('job_role', sa.String(length=40), nullable=False),
        sa.Column('tone', sa.String(length=20), nullable=False),
        sa.Column('language', sa.String(length=8), nullable=False),
        sa.Column('quiet_start', sa.String(length=5), nullable=False),
        sa.Column('quiet_end', sa.String(length=5), nullable=False),
        sa.Column('tracking_enabled', sa.Boolean(), nullable=False),
        sa.Column('proactive_enabled', sa.Boolean(), nullable=False),
        sa.Column('onboarded', sa.Boolean(), nullable=False),
        sa.Column('nudge_weights', sa.JSON(), nullable=False),
        sa.Column('agenda', sa.JSON(), nullable=False),
        sa.Column('agenda_at', _TS, nullable=True),
        sa.Column('messages_since_distill', sa.Integer(), nullable=False),
        sa.Column('created_at', _TS, nullable=False),
        sa.Column('updated_at', _TS, nullable=False),
        sa.PrimaryKeyConstraint('owner'),
    )
    op.create_table(
        'companion_memories',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('owner', sa.String(length=200), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=False),
        sa.Column('pinned', sa.Boolean(), nullable=False),
        sa.Column('uses', sa.Integer(), nullable=False),
        sa.Column('created_at', _TS, nullable=False),
        sa.Column('last_used_at', _TS, nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_companion_memories_owner'), 'companion_memories', ['owner'], unique=False)
    op.create_table(
        'companion_activity',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('owner', sa.String(length=200), nullable=False),
        sa.Column('started_at', _TS, nullable=False),
        sa.Column('seconds', sa.Integer(), nullable=False),
        sa.Column('app', sa.String(length=120), nullable=False),
        sa.Column('title', sa.String(length=300), nullable=False),
        sa.Column('category', sa.String(length=30), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_companion_activity_owner'), 'companion_activity', ['owner'], unique=False)
    op.create_index(op.f('ix_companion_activity_started_at'), 'companion_activity', ['started_at'], unique=False)
    op.create_index(op.f('ix_companion_activity_category'), 'companion_activity', ['category'], unique=False)
    op.create_table(
        'companion_suggestions',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('owner', sa.String(length=200), nullable=False),
        sa.Column('kind', sa.String(length=40), nullable=False),
        sa.Column('title', sa.String(length=300), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('action', sa.JSON(), nullable=False),
        sa.Column('fingerprint', sa.String(length=200), nullable=False),
        sa.Column('score', sa.Float(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('created_at', _TS, nullable=False),
        sa.Column('decided_at', _TS, nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    for col in ('owner', 'kind', 'fingerprint', 'status', 'created_at'):
        op.create_index(op.f(f'ix_companion_suggestions_{col}'), 'companion_suggestions', [col], unique=False)
    op.create_table(
        'companion_messages',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('owner', sa.String(length=200), nullable=False),
        sa.Column('ts', _TS, nullable=False),
        sa.Column('role', sa.String(length=12), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('sources', sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_companion_messages_owner'), 'companion_messages', ['owner'], unique=False)
    op.create_index(op.f('ix_companion_messages_ts'), 'companion_messages', ['ts'], unique=False)
    op.create_table(
        'companion_policy',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('activity_tracking_allowed', sa.Boolean(), nullable=False),
        sa.Column('title_capture', sa.String(length=12), nullable=False),
        sa.Column('retention_days', sa.Integer(), nullable=False),
        sa.Column('excluded_apps', sa.JSON(), nullable=False),
        sa.Column('excluded_keywords', sa.JSON(), nullable=False),
        sa.Column('proactive_allowed', sa.Boolean(), nullable=False),
        sa.Column('max_nudges_per_hour', sa.Integer(), nullable=False),
        sa.Column('memory_learning_allowed', sa.Boolean(), nullable=False),
        sa.Column('updated_by', sa.String(length=200), nullable=False),
        sa.Column('updated_at', _TS, nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('companion_policy')
    op.drop_index(op.f('ix_companion_messages_ts'), table_name='companion_messages')
    op.drop_index(op.f('ix_companion_messages_owner'), table_name='companion_messages')
    op.drop_table('companion_messages')
    for col in ('owner', 'kind', 'fingerprint', 'status', 'created_at'):
        op.drop_index(op.f(f'ix_companion_suggestions_{col}'), table_name='companion_suggestions')
    op.drop_table('companion_suggestions')
    op.drop_index(op.f('ix_companion_activity_category'), table_name='companion_activity')
    op.drop_index(op.f('ix_companion_activity_started_at'), table_name='companion_activity')
    op.drop_index(op.f('ix_companion_activity_owner'), table_name='companion_activity')
    op.drop_table('companion_activity')
    op.drop_index(op.f('ix_companion_memories_owner'), table_name='companion_memories')
    op.drop_table('companion_memories')
    op.drop_table('companion_profiles')
