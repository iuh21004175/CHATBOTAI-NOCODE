"""add stage to customers

Revision ID: c1a8e4f6b2d7
Revises: b9d7e1f3a5c8
Create Date: 2026-09-19 20:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c1a8e4f6b2d7'
down_revision = 'b9d7e1f3a5c8'
branch_labels = None
depends_on = None


def upgrade():
    # server_default: khách đã có tự nhận giai đoạn "new" (Mới), không cần cập nhật từng dòng
    with op.batch_alter_table('customers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('stage', sa.String(length=20), server_default='new', nullable=False))


def downgrade():
    with op.batch_alter_table('customers', schema=None) as batch_op:
        batch_op.drop_column('stage')
