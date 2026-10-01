"""payment_orders: đơn nạp AI Credit qua payOS

Revision ID: 1a2b3c4d5e09
Revises: 1a2b3c4d5e08
Create Date: 2026-09-25 20:00:00.000000

Chỉ thêm bảng mới (không đụng dữ liệu có sẵn).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1a2b3c4d5e09'
down_revision = '1a2b3c4d5e08'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'payment_orders',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('order_code', sa.BigInteger(), nullable=False),
        sa.Column('team_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('amount_vnd', sa.BigInteger(), nullable=False),
        sa.Column('credit_vnd', sa.Numeric(14, 4), nullable=False),
        sa.Column('status', sa.Enum('pending', 'paid', 'cancelled', 'expired', 'failed', name='payment_order_status'), server_default='pending', nullable=False),
        sa.Column('payment_link_id', sa.String(length=64), nullable=True),
        sa.Column('checkout_url', sa.String(length=500), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('paid_at', sa.DateTime(), nullable=True),
        sa.Column('note', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('order_code'),
    )
    with op.batch_alter_table('payment_orders', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_payment_orders_team_id'), ['team_id'], unique=False)


def downgrade():
    op.drop_table('payment_orders')
