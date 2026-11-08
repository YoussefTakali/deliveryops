"""company rules (policy) and personal tech watch

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd6e7f8a9b0c1'
down_revision: Union[str, Sequence[str], None] = 'c5d6e7f8a9b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'policy_documents',
        sa.Column('id', sa.String(40), primary_key=True),
        sa.Column('filename', sa.String(300), nullable=False),
        sa.Column('text', sa.Text(), nullable=False, server_default=''),
        sa.Column('uploaded_by', sa.String(200), nullable=False, server_default=''),
        sa.Column('uploaded_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        'policy_rules',
        sa.Column('id', sa.String(40), primary_key=True),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('text', sa.Text(), nullable=False, server_default=''),
        sa.Column('category', sa.String(30), nullable=False, server_default='autre'),
        sa.Column('action', sa.String(10), nullable=False, server_default='warn'),
        sa.Column('keywords', sa.JSON(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('proposed', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('document_id', sa.String(40), sa.ForeignKey('policy_documents.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_by', sa.String(200), nullable=False, server_default=''),
        sa.Column('updated_by', sa.String(200), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_policy_rules_document_id', 'policy_rules', ['document_id'])
    op.create_table(
        'tech_watch_profiles',
        sa.Column('owner', sa.String(200), primary_key=True),
        sa.Column('extra_topics', sa.JSON(), nullable=True),
        sa.Column('muted_topics', sa.JSON(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        'tech_watch_briefings',
        sa.Column('id', sa.String(40), primary_key=True),
        sa.Column('owner', sa.String(200), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('topics', sa.JSON(), nullable=True),
        sa.Column('items', sa.JSON(), nullable=True),
        sa.Column('summary', sa.Text(), nullable=False, server_default=''),
        sa.Column('llm_used', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index('ix_tech_watch_briefings_owner', 'tech_watch_briefings', ['owner'])
    op.create_index('ix_tech_watch_briefings_created_at', 'tech_watch_briefings', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_tech_watch_briefings_created_at', table_name='tech_watch_briefings')
    op.drop_index('ix_tech_watch_briefings_owner', table_name='tech_watch_briefings')
    op.drop_table('tech_watch_briefings')
    op.drop_table('tech_watch_profiles')
    op.drop_index('ix_policy_rules_document_id', table_name='policy_rules')
    op.drop_table('policy_rules')
    op.drop_table('policy_documents')
