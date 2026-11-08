"""automations (workflows, runs, files, recordings)

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-18 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'automations',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('owner', sa.String(length=200), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('trigger', sa.JSON(), nullable=False),
        sa.Column('steps', sa.JSON(), nullable=False),
        sa.Column('inputs', sa.JSON(), nullable=False),
        sa.Column('require_confirmation', sa.Boolean(), nullable=False),
        sa.Column('approved_script_hashes', sa.JSON(), nullable=False),
        sa.Column('source', sa.JSON(), nullable=False),
        sa.Column('next_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_run_status', sa.String(length=30), nullable=True),
        sa.Column('mail_trigger_checked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_automations_owner'), 'automations', ['owner'], unique=False)
    op.create_index(op.f('ix_automations_next_run_at'), 'automations', ['next_run_at'], unique=False)

    op.create_table(
        'automation_runs',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('automation_id', sa.String(length=40), nullable=False),
        sa.Column('owner', sa.String(length=200), nullable=False),
        sa.Column('trigger', sa.String(length=20), nullable=False),
        sa.Column('dry_run', sa.Boolean(), nullable=False),
        sa.Column('status', sa.String(length=30), nullable=False),
        sa.Column('context', sa.JSON(), nullable=False),
        sa.Column('log', sa.JSON(), nullable=False),
        sa.Column('error', sa.Text(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['automation_id'], ['automations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_automation_runs_automation_id'), 'automation_runs', ['automation_id'], unique=False)
    op.create_index(op.f('ix_automation_runs_owner'), 'automation_runs', ['owner'], unique=False)

    op.create_table(
        'automation_files',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('owner', sa.String(length=200), nullable=False),
        sa.Column('automation_id', sa.String(length=40), nullable=True),
        sa.Column('run_id', sa.String(length=40), nullable=True),
        sa.Column('name', sa.String(length=300), nullable=False),
        sa.Column('content_type', sa.String(length=200), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('data', sa.LargeBinary(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['automation_id'], ['automations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['run_id'], ['automation_runs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_automation_files_owner'), 'automation_files', ['owner'], unique=False)
    op.create_index(op.f('ix_automation_files_automation_id'), 'automation_files', ['automation_id'], unique=False)
    op.create_index(op.f('ix_automation_files_run_id'), 'automation_files', ['run_id'], unique=False)

    op.create_table(
        'automation_recordings',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('owner', sa.String(length=200), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('duration_s', sa.Float(), nullable=False),
        sa.Column('events', sa.JSON(), nullable=False),
        sa.Column('narration', sa.JSON(), nullable=False),
        sa.Column('keyframes', sa.JSON(), nullable=False),
        sa.Column('analysis', sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_automation_recordings_owner'), 'automation_recordings', ['owner'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_automation_recordings_owner'), table_name='automation_recordings')
    op.drop_table('automation_recordings')
    op.drop_index(op.f('ix_automation_files_run_id'), table_name='automation_files')
    op.drop_index(op.f('ix_automation_files_automation_id'), table_name='automation_files')
    op.drop_index(op.f('ix_automation_files_owner'), table_name='automation_files')
    op.drop_table('automation_files')
    op.drop_index(op.f('ix_automation_runs_owner'), table_name='automation_runs')
    op.drop_index(op.f('ix_automation_runs_automation_id'), table_name='automation_runs')
    op.drop_table('automation_runs')
    op.drop_index(op.f('ix_automations_next_run_at'), table_name='automations')
    op.drop_index(op.f('ix_automations_owner'), table_name='automations')
    op.drop_table('automations')
