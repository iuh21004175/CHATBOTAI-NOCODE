"""context engine: bot_settings (tier, memory, summary, rag, decision, cost knobs)

Revision ID: 1a2b3c4d5e01
Revises: d3f6a8c1e9b2
Create Date: 2026-09-21 09:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1a2b3c4d5e01'
down_revision = 'd3f6a8c1e9b2'
branch_labels = None
depends_on = None

_NEW_COLUMNS = (
    'config_tier', 'rag_enabled', 'recent_message_limit', 'recent_token_limit', 'summary_enabled',
    'summary_trigger_tokens', 'summary_max_tokens', 'structured_memory_enabled', 'memory_max_items',
    'memory_min_confidence', 'intent_tracking_enabled', 'intent_confidence_threshold', 'slot_filling_enabled',
    'slot_completion_threshold', 'rag_top_k', 'rag_rerank_top_n', 'rag_distance_threshold',
    'rag_max_context_tokens', 'max_candidate_count', 'clarification_enabled', 'max_clarification_turns',
    'context_pressure_warning', 'context_pressure_hard_limit', 'low_confidence_reply_mode',
    'low_confidence_decline_message', 'low_confidence_clarify_message', 'max_context_tokens',
)


def upgrade():
    # Mọi cột đều NOT NULL kèm server_default an toàn -> bot cũ tự nhận mặc định, không cần cập nhật từng dòng.
    with op.batch_alter_table('bot_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('config_tier', sa.Enum('basic', 'advanced', 'expert', name='bot_config_tier'), server_default='basic', nullable=False))
        batch_op.add_column(sa.Column('rag_enabled', sa.Boolean(), server_default='1', nullable=False))
        batch_op.add_column(sa.Column('recent_message_limit', sa.Integer(), server_default='10', nullable=False))
        batch_op.add_column(sa.Column('recent_token_limit', sa.Integer(), server_default='2000', nullable=False))
        batch_op.add_column(sa.Column('summary_enabled', sa.Boolean(), server_default='1', nullable=False))
        batch_op.add_column(sa.Column('summary_trigger_tokens', sa.Integer(), server_default='4000', nullable=False))
        batch_op.add_column(sa.Column('summary_max_tokens', sa.Integer(), server_default='500', nullable=False))
        batch_op.add_column(sa.Column('structured_memory_enabled', sa.Boolean(), server_default='1', nullable=False))
        batch_op.add_column(sa.Column('memory_max_items', sa.Integer(), server_default='30', nullable=False))
        batch_op.add_column(sa.Column('memory_min_confidence', sa.Float(), server_default='0.70', nullable=False))
        batch_op.add_column(sa.Column('intent_tracking_enabled', sa.Boolean(), server_default='1', nullable=False))
        batch_op.add_column(sa.Column('intent_confidence_threshold', sa.Float(), server_default='0.70', nullable=False))
        batch_op.add_column(sa.Column('slot_filling_enabled', sa.Boolean(), server_default='1', nullable=False))
        batch_op.add_column(sa.Column('slot_completion_threshold', sa.Float(), server_default='0.80', nullable=False))
        batch_op.add_column(sa.Column('rag_top_k', sa.Integer(), server_default='8', nullable=False))
        batch_op.add_column(sa.Column('rag_rerank_top_n', sa.Integer(), server_default='5', nullable=False))
        batch_op.add_column(sa.Column('rag_distance_threshold', sa.Float(), server_default='1.50', nullable=False))
        batch_op.add_column(sa.Column('rag_max_context_tokens', sa.Integer(), server_default='3000', nullable=False))
        batch_op.add_column(sa.Column('max_candidate_count', sa.Integer(), server_default='5', nullable=False))
        batch_op.add_column(sa.Column('clarification_enabled', sa.Boolean(), server_default='1', nullable=False))
        batch_op.add_column(sa.Column('max_clarification_turns', sa.Integer(), server_default='2', nullable=False))
        batch_op.add_column(sa.Column('context_pressure_warning', sa.Float(), server_default='0.80', nullable=False))
        batch_op.add_column(sa.Column('context_pressure_hard_limit', sa.Float(), server_default='0.90', nullable=False))
        batch_op.add_column(sa.Column('low_confidence_reply_mode', sa.Enum('decline', 'ask_clarify', name='low_confidence_reply_mode'), server_default='ask_clarify', nullable=False))
        batch_op.add_column(sa.Column('low_confidence_decline_message', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('low_confidence_clarify_message', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('max_context_tokens', sa.Integer(), server_default='8000', nullable=False))

    # min_similarity (cosine) được thay bằng rag_distance_threshold (bình phương L2 của Chroma, d = 2·(1−cos)) —
    # quy đổi để bot đã chỉnh độ liên quan ở Bước 1 giữ nguyên hành vi. Bot đã chỉnh khác mặc định 0,25 chuyển sang
    # tier 'advanced' (tier 'basic' dùng mặc định và sẽ lặng lẽ bỏ giá trị họ đã chọn).
    op.execute("UPDATE bot_settings SET rag_distance_threshold = ROUND(2 * (1 - min_similarity), 2)")
    op.execute("UPDATE bot_settings SET config_tier = 'advanced' WHERE ABS(min_similarity - 0.25) > 0.001")


def downgrade():
    with op.batch_alter_table('bot_settings', schema=None) as batch_op:
        for column in reversed(_NEW_COLUMNS):
            batch_op.drop_column(column)
