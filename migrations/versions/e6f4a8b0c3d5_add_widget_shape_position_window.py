"""add widget_shape, widget_position, widget_window to bot_settings

Revision ID: e6f4a8b0c3d5
Revises: d5e3f7a9b2c4
Create Date: 2026-09-19 13:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e6f4a8b0c3d5'
down_revision = 'd5e3f7a9b2c4'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('bot_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('widget_shape', sa.String(length=10), server_default='round', nullable=False))
        batch_op.add_column(sa.Column('widget_position', sa.String(length=10), server_default='right', nullable=False))
        batch_op.add_column(sa.Column('widget_window', sa.String(length=4), server_default='md', nullable=False))


def downgrade():
    with op.batch_alter_table('bot_settings', schema=None) as batch_op:
        batch_op.drop_column('widget_window')
        batch_op.drop_column('widget_position')
        batch_op.drop_column('widget_shape')
