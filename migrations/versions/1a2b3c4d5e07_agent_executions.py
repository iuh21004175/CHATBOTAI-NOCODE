"""agent_executions: mỗi lượt trả lời ở chế độ AI Agent một dòng (số liệu vận hành, kể cả lượt lỗi/hết giờ)

Revision ID: 1a2b3c4d5e07
Revises: 1a2b3c4d5e06
Create Date: 2026-09-25 10:00:00.000000

Chỉ thêm bảng mới (không đụng dữ liệu có sẵn). Chi phí không nằm ở đây — xem core/context_engine/cost.py và Phase D.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1a2b3c4d5e07'
down_revision = '1a2b3c4d5e06'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'agent_executions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('job_id', sa.String(length=64), nullable=False),
        sa.Column('bot_id', sa.Integer(), nullable=False),
        sa.Column('conversation_id', sa.Integer(), nullable=True),
        sa.Column('message_id', sa.Integer(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(length=30), nullable=False),
        sa.Column('iterations_used', sa.Integer(), server_default='0', nullable=False),
        sa.Column('tool_calls_used', sa.Integer(), server_default='0', nullable=False),
        sa.Column('total_llm_calls', sa.Integer(), server_default='0', nullable=False),
        sa.Column('stop_reason', sa.String(length=100), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['bot_id'], ['bots.id']),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id']),
        sa.ForeignKeyConstraint(['message_id'], ['messages.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('job_id'),
    )
    with op.batch_alter_table('agent_executions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_agent_executions_bot_id'), ['bot_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_agent_executions_conversation_id'), ['conversation_id'], unique=False)


def downgrade():
    op.drop_table('agent_executions')
