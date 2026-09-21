"""add widget_icon_path to bot_settings

Revision ID: d3f6a8c1e9b2
Revises: c1a8e4f6b2d7
Create Date: 2026-09-20 22:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd3f6a8c1e9b2'
down_revision = 'c1a8e4f6b2d7'
branch_labels = None
depends_on = None


def upgrade():
    # NULL: chưa có bot nào tải icon tuỳ chỉnh, không cần cập nhật từng dòng
    with op.batch_alter_table('bot_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('widget_icon_path', sa.String(length=500), nullable=True))


def downgrade():
    with op.batch_alter_table('bot_settings', schema=None) as batch_op:
        batch_op.drop_column('widget_icon_path')
