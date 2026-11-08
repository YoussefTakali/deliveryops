"""groups & project management (Unix-style permissions) + companion.share_work

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-09-19 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, Sequence[str], None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column('companion_profiles', sa.Column('share_work', sa.Boolean(), nullable=False, server_default=sa.false()))

    op.create_table(
        'groups',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('parent_id', sa.String(length=40), nullable=True),
        sa.Column('color', sa.String(length=16), nullable=False),
        sa.Column('created_by', sa.String(length=200), nullable=False),
        sa.Column('created_at', _TS, nullable=False),
        sa.Column('updated_at', _TS, nullable=False),
        sa.ForeignKeyConstraint(['parent_id'], ['groups.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_groups_parent_id'), 'groups', ['parent_id'], unique=False)
    op.create_index(op.f('ix_groups_created_by'), 'groups', ['created_by'], unique=False)

    op.create_table(
        'group_memberships',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('group_id', sa.String(length=40), nullable=False),
        sa.Column('member_owner', sa.String(length=200), nullable=False),
        sa.Column('group_role', sa.String(length=20), nullable=False),
        sa.Column('title', sa.String(length=120), nullable=False),
        sa.Column('created_at', _TS, nullable=False),
        sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_group_memberships_group_id'), 'group_memberships', ['group_id'], unique=False)
    op.create_index(op.f('ix_group_memberships_member_owner'), 'group_memberships', ['member_owner'], unique=False)

    op.create_table(
        'projects',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('owner_user', sa.String(length=200), nullable=False),
        sa.Column('group_id', sa.String(length=40), nullable=True),
        sa.Column('mode', sa.Integer(), nullable=False),
        sa.Column('color', sa.String(length=16), nullable=False),
        sa.Column('due', sa.Date(), nullable=True),
        sa.Column('created_at', _TS, nullable=False),
        sa.Column('updated_at', _TS, nullable=False),
        sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_projects_status'), 'projects', ['status'], unique=False)
    op.create_index(op.f('ix_projects_owner_user'), 'projects', ['owner_user'], unique=False)
    op.create_index(op.f('ix_projects_group_id'), 'projects', ['group_id'], unique=False)

    op.create_table(
        'work_items',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('project_id', sa.String(length=40), nullable=True),
        sa.Column('group_id', sa.String(length=40), nullable=True),
        sa.Column('title', sa.String(length=300), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('priority', sa.String(length=10), nullable=False),
        sa.Column('owner_user', sa.String(length=200), nullable=False),
        sa.Column('assignee_owner', sa.String(length=200), nullable=True),
        sa.Column('mode', sa.Integer(), nullable=False),
        sa.Column('due', sa.Date(), nullable=True),
        sa.Column('order', sa.Float(), nullable=False),
        sa.Column('created_at', _TS, nullable=False),
        sa.Column('updated_at', _TS, nullable=False),
        sa.Column('done_at', _TS, nullable=True),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    for col in ('project_id', 'group_id', 'status', 'owner_user', 'assignee_owner'):
        op.create_index(op.f(f'ix_work_items_{col}'), 'work_items', [col], unique=False)

    op.create_table(
        'group_activity',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('group_id', sa.String(length=40), nullable=True),
        sa.Column('actor', sa.String(length=200), nullable=False),
        sa.Column('verb', sa.String(length=40), nullable=False),
        sa.Column('summary', sa.String(length=400), nullable=False),
        sa.Column('ref_id', sa.String(length=40), nullable=True),
        sa.Column('ts', _TS, nullable=False),
        sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_group_activity_group_id'), 'group_activity', ['group_id'], unique=False)
    op.create_index(op.f('ix_group_activity_actor'), 'group_activity', ['actor'], unique=False)
    op.create_index(op.f('ix_group_activity_ts'), 'group_activity', ['ts'], unique=False)


def downgrade() -> None:
    for ix in ('group_id', 'actor', 'ts'):
        op.drop_index(op.f(f'ix_group_activity_{ix}'), table_name='group_activity')
    op.drop_table('group_activity')
    for col in ('project_id', 'group_id', 'status', 'owner_user', 'assignee_owner'):
        op.drop_index(op.f(f'ix_work_items_{col}'), table_name='work_items')
    op.drop_table('work_items')
    for ix in ('status', 'owner_user', 'group_id'):
        op.drop_index(op.f(f'ix_projects_{ix}'), table_name='projects')
    op.drop_table('projects')
    op.drop_index(op.f('ix_group_memberships_member_owner'), table_name='group_memberships')
    op.drop_index(op.f('ix_group_memberships_group_id'), table_name='group_memberships')
    op.drop_table('group_memberships')
    op.drop_index(op.f('ix_groups_created_by'), table_name='groups')
    op.drop_index(op.f('ix_groups_parent_id'), table_name='groups')
    op.drop_table('groups')
    op.drop_column('companion_profiles', 'share_work')
