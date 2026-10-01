"""AI Credit (Phase D): execution_costs, credit_accounts, credit_transactions + cột trần chi phí/câu hết Credit trên bot_settings

Revision ID: 1a2b3c4d5e08
Revises: 1a2b3c4d5e07
Create Date: 2026-09-25 18:30:00.000000

Chỉ THÊM bảng/cột mới; cột mới trên bot_settings có server_default nên bot đang có nhận giá trị mặc định ngay (không đụng dữ liệu cũ).
Không cấp Credit cho team đã có trong migration này: team chưa có tài khoản Credit được cấp Credit dùng thử đúng 1 lần, lúc lần đầu cần
tới (app/credits/service.py:ensure_account).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1a2b3c4d5e08'
down_revision = '1a2b3c4d5e07'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'credit_accounts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('team_id', sa.Integer(), nullable=False),
        sa.Column('balance_vnd', sa.Numeric(14, 4), server_default='0', nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('team_id'),
    )
    op.create_table(
        'execution_costs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('execution_id', sa.Integer(), nullable=False),
        sa.Column('team_id', sa.Integer(), nullable=False),
        sa.Column('bot_id', sa.Integer(), nullable=False),
        sa.Column('llm_input_tokens', sa.Integer(), server_default='0', nullable=False),
        sa.Column('llm_output_tokens', sa.Integer(), server_default='0', nullable=False),
        sa.Column('llm_cache_hit_tokens', sa.Integer(), server_default='0', nullable=False),
        sa.Column('llm_cache_miss_tokens', sa.Integer(), server_default='0', nullable=False),
        sa.Column('llm_cost_vnd', sa.Numeric(14, 4), server_default='0', nullable=False),
        sa.Column('tool_cost_vnd', sa.Numeric(14, 4), server_default='0', nullable=False),
        sa.Column('infra_cost_vnd', sa.Numeric(14, 4), server_default='0', nullable=False),
        sa.Column('total_cost_vnd', sa.Numeric(14, 4), server_default='0', nullable=False),
        sa.Column('markup_multiplier', sa.Numeric(8, 4), server_default='1', nullable=False),
        sa.Column('billed_vnd', sa.Numeric(14, 4), server_default='0', nullable=False),
        sa.Column('charged_vnd', sa.Numeric(14, 4), server_default='0', nullable=False),
        sa.Column('uncollected_vnd', sa.Numeric(14, 4), server_default='0', nullable=False),
        sa.Column('usage_reported', sa.Boolean(), server_default='1', nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['bot_id'], ['bots.id']),
        sa.ForeignKeyConstraint(['execution_id'], ['agent_executions.id']),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('execution_id'),
    )
    with op.batch_alter_table('execution_costs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_execution_costs_bot_id'), ['bot_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_execution_costs_team_id'), ['team_id'], unique=False)
    op.create_table(
        'credit_transactions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('team_id', sa.Integer(), nullable=False),
        sa.Column('execution_id', sa.Integer(), nullable=True),
        sa.Column('type', sa.Enum('trial_grant', 'execution_charge', 'reserve', 'release', 'topup', 'adjustment', name='credit_transaction_type'), nullable=False),
        sa.Column('amount_vnd', sa.Numeric(14, 4), nullable=False),
        sa.Column('balance_after_vnd', sa.Numeric(14, 4), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('note', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['execution_id'], ['agent_executions.id']),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('credit_transactions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_credit_transactions_execution_id'), ['execution_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_credit_transactions_team_id'), ['team_id'], unique=False)
    with op.batch_alter_table('bot_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('max_cost_per_execution_vnd', sa.Numeric(12, 2), server_default='1000', nullable=False))
        batch_op.add_column(sa.Column('out_of_credit_message', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('bot_settings', schema=None) as batch_op:
        batch_op.drop_column('out_of_credit_message')
        batch_op.drop_column('max_cost_per_execution_vnd')
    op.drop_table('credit_transactions')
    op.drop_table('execution_costs')
    op.drop_table('credit_accounts')
