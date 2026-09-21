"""add min_similarity to bot_settings

Revision ID: b9d7e1f3a5c8
Revises: a8c6d0e2f4b7
Create Date: 2026-09-19 18:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b9d7e1f3a5c8'
down_revision = 'a8c6d0e2f4b7'
branch_labels = None
depends_on = None


def upgrade():
    # server_default: các bot đã có tự nhận 0.25 (mặc định đã đo), không cần cập nhật từng dòng
    with op.batch_alter_table('bot_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('min_similarity', sa.Float(), server_default='0.25', nullable=False))


def downgrade():
    with op.batch_alter_table('bot_settings', schema=None) as batch_op:
        batch_op.drop_column('min_similarity')
