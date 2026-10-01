"""Đổi module "Đọc tài liệu" sang đọc ảnh/PDF bản scan bằng DeepSeek vision (thay OCR cục bộ) — TỐN AI Credit thật, giống hệt khuôn
module_analysis_charge của Phase M (xem app/credits/service.py:settle_attachment_vision, core/doc_reader/vision_reader.py).

credit_transactions.type: thêm 'attachment_vision_charge'.
message_attachments: thêm vision_cost_vnd (giá vốn LLM thật) + vision_charged_vnd (Credit thực thu) — chỉ khác 0 khi extract_method='vision'.

Revision ID: d7f9b1c3e5a7
Revises: c3d5e7f9a1b3
Create Date: 2026-10-01 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd7f9b1c3e5a7'
down_revision = 'c3d5e7f9a1b3'
branch_labels = None
depends_on = None

OLD_CREDIT_TYPES = ('trial_grant', 'execution_charge', 'reserve', 'release', 'topup', 'adjustment', 'module_analysis_charge')
NEW_CREDIT_TYPES = OLD_CREDIT_TYPES + ('attachment_vision_charge',)


def upgrade():
    with op.batch_alter_table('credit_transactions', schema=None) as batch_op:
        batch_op.alter_column(
            'type', existing_type=sa.Enum(*OLD_CREDIT_TYPES, name='credit_transaction_type'),
            type_=sa.Enum(*NEW_CREDIT_TYPES, name='credit_transaction_type'), existing_nullable=False,
        )
    with op.batch_alter_table('message_attachments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('vision_cost_vnd', sa.Numeric(14, 4), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('vision_charged_vnd', sa.Numeric(14, 4), server_default='0', nullable=False))


def downgrade():
    with op.batch_alter_table('message_attachments', schema=None) as batch_op:
        batch_op.drop_column('vision_charged_vnd')
        batch_op.drop_column('vision_cost_vnd')
    # Thu hẹp enum sẽ LỖI (đúng ý) nếu còn dòng 'attachment_vision_charge': người vận hành tự quyết định trước khi downgrade.
    with op.batch_alter_table('credit_transactions', schema=None) as batch_op:
        batch_op.alter_column(
            'type', existing_type=sa.Enum(*NEW_CREDIT_TYPES, name='credit_transaction_type'),
            type_=sa.Enum(*OLD_CREDIT_TYPES, name='credit_transaction_type'), existing_nullable=False,
        )
