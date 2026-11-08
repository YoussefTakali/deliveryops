"""account security (lockout, 2FA, device sessions, password reset) and addressed notifications

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b4c5d6e7f8a9'
down_revision: Union[str, Sequence[str], None] = 'a3b4c5d6e7f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('notifications', sa.Column('audience', sa.String(60), nullable=False, server_default=''))
    op.add_column('notifications', sa.Column('recipient', sa.String(200), nullable=False, server_default=''))
    op.create_index('ix_notifications_recipient', 'notifications', ['recipient'])

    op.add_column('auth_users', sa.Column('failed_logins', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('auth_users', sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True))
    op.add_column('auth_users', sa.Column('mfa_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('auth_users', sa.Column('mfa_secret_enc', sa.Text(), nullable=False, server_default=''))
    op.add_column('auth_users', sa.Column('mfa_recovery', sa.JSON(), nullable=True))
    op.add_column('auth_users', sa.Column('mfa_last_step', sa.Integer(), nullable=False, server_default='0'))

    op.add_column('refresh_tokens', sa.Column('user_agent', sa.String(300), nullable=False, server_default=''))
    op.add_column('refresh_tokens', sa.Column('ip', sa.String(64), nullable=False, server_default=''))
    op.add_column('refresh_tokens', sa.Column('signed_in_at', sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        'password_reset_tokens',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.String(40), sa.ForeignKey('auth_users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('token_hash', sa.String(80), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_password_reset_tokens_user_id', 'password_reset_tokens', ['user_id'])
    op.create_index('ix_password_reset_tokens_token_hash', 'password_reset_tokens', ['token_hash'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_password_reset_tokens_token_hash', table_name='password_reset_tokens')
    op.drop_index('ix_password_reset_tokens_user_id', table_name='password_reset_tokens')
    op.drop_table('password_reset_tokens')
    for col in ('signed_in_at', 'ip', 'user_agent'):
        op.drop_column('refresh_tokens', col)
    for col in ('mfa_last_step', 'mfa_recovery', 'mfa_secret_enc', 'mfa_enabled', 'locked_until', 'failed_logins'):
        op.drop_column('auth_users', col)
    op.drop_index('ix_notifications_recipient', table_name='notifications')
    op.drop_column('notifications', 'recipient')
    op.drop_column('notifications', 'audience')
