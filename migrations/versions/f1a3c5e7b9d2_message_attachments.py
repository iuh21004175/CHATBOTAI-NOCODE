"""message_attachments: tệp khách gửi trong widget (module "Đọc tài liệu")

Revision ID: f1a3c5e7b9d2
Revises: e2f5a7c9b1d3
Create Date: 2026-09-29 10:00:00.000000

Chỉ thêm bảng mới (không đụng dữ liệu có sẵn). Loại module "document_reader" được seed lúc chạy bởi app/modules/service.py:ensure_module_types().
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f1a3c5e7b9d2'
down_revision = 'e2f5a7c9b1d3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'message_attachments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('bot_id', sa.Integer(), nullable=False),
        sa.Column('conversation_id', sa.Integer(), nullable=True),
        sa.Column('message_id', sa.Integer(), nullable=True),
        sa.Column('visitor_id', sa.String(length=255), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('content_type', sa.String(length=100), nullable=True),
        sa.Column('size_bytes', sa.Integer(), server_default='0', nullable=False),
        sa.Column('storage_path', sa.String(length=500), nullable=False),
        sa.Column('status', sa.Enum('processing', 'ready', 'failed', name='attachment_status'), server_default='processing', nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('extract_method', sa.String(length=30), nullable=True),
        sa.Column('char_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('chunk_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('truncated', sa.Boolean(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('processed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['bot_id'], ['bots.id']),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id']),
        sa.ForeignKeyConstraint(['message_id'], ['messages.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('message_attachments', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_message_attachments_bot_id'), ['bot_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_message_attachments_conversation_id'), ['conversation_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_message_attachments_message_id'), ['message_id'], unique=False)


def downgrade():
    op.drop_table('message_attachments')
