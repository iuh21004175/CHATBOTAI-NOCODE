"""add per-document chunk config and draft status

Revision ID: a8c6d0e2f4b7
Revises: f7a5b9c1d4e6
Create Date: 2026-09-19 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a8c6d0e2f4b7'
down_revision = 'f7a5b9c1d4e6'
branch_labels = None
depends_on = None

OLD_STATUSES = ('pending', 'processing', 'trained', 'failed')
NEW_STATUSES = OLD_STATUSES + ('draft',)  # thêm cuối danh sách: MySQL chỉ đổi metadata, không viết lại dữ liệu


def upgrade():
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('chunk_size', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('chunk_overlap', sa.Integer(), nullable=True))
        batch_op.alter_column(
            'status',
            existing_type=sa.Enum(*OLD_STATUSES, name='document_status'),
            type_=sa.Enum(*NEW_STATUSES, name='document_status'),
            existing_nullable=True,
        )


def downgrade():
    # Tài liệu đang ở "draft" chưa được huấn luyện: đưa về "pending" để giá trị enum cũ vẫn hợp lệ
    op.execute("UPDATE documents SET status = 'pending' WHERE status = 'draft'")
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.alter_column(
            'status',
            existing_type=sa.Enum(*NEW_STATUSES, name='document_status'),
            type_=sa.Enum(*OLD_STATUSES, name='document_status'),
            existing_nullable=True,
        )
        batch_op.drop_column('chunk_overlap')
        batch_op.drop_column('chunk_size')
