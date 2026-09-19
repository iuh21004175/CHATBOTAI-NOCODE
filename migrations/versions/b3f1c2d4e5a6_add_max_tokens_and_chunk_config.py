"""add max_tokens and chunk config to bot_settings

Revision ID: b3f1c2d4e5a6
Revises: ac79baccb0bb
Create Date: 2026-09-19 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b3f1c2d4e5a6'
down_revision = 'ac79baccb0bb'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('bot_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('max_tokens', sa.Integer(), server_default='500', nullable=False))
        batch_op.add_column(sa.Column('chunk_size', sa.Integer(), server_default='450', nullable=False))
        batch_op.add_column(sa.Column('chunk_overlap', sa.Integer(), server_default='60', nullable=False))


def downgrade():
    with op.batch_alter_table('bot_settings', schema=None) as batch_op:
        batch_op.drop_column('chunk_overlap')
        batch_op.drop_column('chunk_size')
        batch_op.drop_column('max_tokens')
