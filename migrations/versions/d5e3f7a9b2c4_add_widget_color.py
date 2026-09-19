"""add widget_color to bot_settings

Revision ID: d5e3f7a9b2c4
Revises: c4d2e6f8a1b3
Create Date: 2026-09-19 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd5e3f7a9b2c4'
down_revision = 'c4d2e6f8a1b3'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('bot_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('widget_color', sa.String(length=7), server_default='#1D4ED8', nullable=False))


def downgrade():
    with op.batch_alter_table('bot_settings', schema=None) as batch_op:
        batch_op.drop_column('widget_color')
