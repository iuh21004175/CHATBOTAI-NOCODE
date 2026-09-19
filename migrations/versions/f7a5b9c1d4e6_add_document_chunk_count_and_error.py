"""add chunk_count and error_message to documents

Revision ID: f7a5b9c1d4e6
Revises: e6f4a8b0c3d5
Create Date: 2026-09-19 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f7a5b9c1d4e6'
down_revision = 'e6f4a8b0c3d5'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('chunk_count', sa.Integer(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('error_message', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.drop_column('error_message')
        batch_op.drop_column('chunk_count')
