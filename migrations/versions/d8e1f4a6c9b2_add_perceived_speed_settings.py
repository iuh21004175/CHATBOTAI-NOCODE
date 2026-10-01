"""add perceived-speed settings (progress/fillers/async toggles + custom progress texts) to bot_settings

Revision ID: d8e1f4a6c9b2
Revises: 1a2b3c4d5e10
Create Date: 2026-09-28 09:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd8e1f4a6c9b2'
down_revision = '1a2b3c4d5e10'
branch_labels = None
depends_on = None


def upgrade():
    # server_default '1': bot đã có tự nhận MẶC ĐỊNH BẬT cả 3 kỹ thuật (đúng yêu cầu), không cần cập nhật từng dòng.
    with op.batch_alter_table('bot_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('speed_progress_enabled', sa.Boolean(), server_default='1', nullable=False))
        batch_op.add_column(sa.Column('speed_fillers_enabled', sa.Boolean(), server_default='1', nullable=False))
        batch_op.add_column(sa.Column('speed_async_enabled', sa.Boolean(), server_default='1', nullable=False))
        batch_op.add_column(sa.Column('speed_progress_text_analyzing', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('speed_progress_text_searching', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('speed_progress_text_acting', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('speed_progress_text_composing', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('bot_settings', schema=None) as batch_op:
        batch_op.drop_column('speed_progress_text_composing')
        batch_op.drop_column('speed_progress_text_acting')
        batch_op.drop_column('speed_progress_text_searching')
        batch_op.drop_column('speed_progress_text_analyzing')
        batch_op.drop_column('speed_async_enabled')
        batch_op.drop_column('speed_fillers_enabled')
        batch_op.drop_column('speed_progress_enabled')
