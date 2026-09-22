"""context engine: messages.decision_trace + usage_* (token/cache-hit của lệnh gọi DeepSeek chính)

Revision ID: 1a2b3c4d5e03
Revises: 1a2b3c4d5e02
Create Date: 2026-09-21 09:02:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1a2b3c4d5e03'
down_revision = '1a2b3c4d5e02'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.add_column(sa.Column('decision_trace', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('usage_prompt_tokens', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('usage_completion_tokens', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('usage_cache_hit_tokens', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('usage_cache_miss_tokens', sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table('messages', schema=None) as batch_op:
        for column in (
            'usage_cache_miss_tokens', 'usage_cache_hit_tokens', 'usage_completion_tokens',
            'usage_prompt_tokens', 'decision_trace',
        ):
            batch_op.drop_column(column)
