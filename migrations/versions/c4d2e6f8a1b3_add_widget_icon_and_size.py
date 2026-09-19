"""add widget_icon and widget_size to bot_settings

Revision ID: c4d2e6f8a1b3
Revises: b3f1c2d4e5a6
Create Date: 2026-09-19 11:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c4d2e6f8a1b3'
down_revision = 'b3f1c2d4e5a6'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('bot_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('widget_icon', sa.String(length=20), server_default='chat', nullable=False))
        batch_op.add_column(sa.Column('widget_size', sa.Integer(), server_default='56', nullable=False))


def downgrade():
    with op.batch_alter_table('bot_settings', schema=None) as batch_op:
        batch_op.drop_column('widget_size')
        batch_op.drop_column('widget_icon')
