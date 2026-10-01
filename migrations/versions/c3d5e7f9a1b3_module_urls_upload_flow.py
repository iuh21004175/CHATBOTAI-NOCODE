"""module_urls: chuyển từ khai báo URL (server tự crawl HTTP) sang khai báo bằng TẢI LÊN file .zip trang đã lưu ("Webpage, Complete") — xem
docs/WEBSITE_ACTION_ENGINE.md và core/website_actions/zip_extract.py + core/website_actions/domain_detect.py.

Bỏ (không còn ý nghĩa vì không còn crawl):
  url (địa chỉ server tự fetch), domain (hostname suy từ url đó), crawled_at, raw_html_storage_key (HTML đã crawl)
Thêm:
  source_url (domain NGƯỜI DÙNG ĐÃ XÁC NHẬN — dùng để so khớp Origin/bot_domains, thay cho "domain" cũ)
  detected_domain (domain hệ thống TỰ ĐOÁN từ nội dung file — chỉ để gợi ý, KHÔNG dùng so khớp bảo mật)
  upload_storage_key (key file .zip gốc trên MinIO), entry_html_filename (tên file .html chính trong zip)
  upload_status (tiến độ xử lý RIÊNG của url này), created_at, updated_at, extracted_at (thay "crawled_at")

bot_modules.status: thêm giá trị 'awaiting_domain_confirmation' — module đã phân tích xong (có module_actions) nhưng còn URL bắt buộc (theo
module_type_url_roles) chưa qua bước xác nhận domain thì KHÔNG được tự chuyển 'ready' (xem app/modules/service.py:_recompute_module_status).

migrations/versions/1a2b3c4d5e10_website_action_engine.py (tạo module_urls lần đầu) chưa từng chạy trên dữ liệu thật của bản phát hành nào (tính
năng còn đang phát triển trên nhánh này) nên không cần data migration cho dữ liệu module_urls cũ — bảng được coi là rỗng về mặt nghiệp vụ.

Revision ID: c3d5e7f9a1b3
Revises: f1a3c5e7b9d2
Create Date: 2026-09-30 09:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3d5e7f9a1b3'
down_revision = 'f1a3c5e7b9d2'
branch_labels = None
depends_on = None

URL_ROLES = ('product_listing', 'product_detail', 'cart', 'checkout', 'other')
UPLOAD_STATUSES = ('awaiting_upload', 'uploaded', 'extracted', 'domain_confirmed', 'failed')
OLD_MODULE_STATUSES = ('pending', 'analyzing', 'ready', 'failed')
NEW_MODULE_STATUSES = ('pending', 'analyzing', 'awaiting_domain_confirmation', 'ready', 'failed')


def upgrade():
    with op.batch_alter_table('module_urls', schema=None) as batch_op:
        batch_op.drop_column('url')
        batch_op.drop_column('domain')
        batch_op.drop_column('crawled_at')
        batch_op.drop_column('raw_html_storage_key')
        batch_op.add_column(sa.Column('source_url', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('detected_domain', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('upload_storage_key', sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column('entry_html_filename', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column(
            'upload_status', sa.Enum(*UPLOAD_STATUSES, name='module_url_upload_status'),
            server_default='awaiting_upload', nullable=False,
        ))
        batch_op.add_column(sa.Column('created_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('updated_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('extracted_at', sa.DateTime(), nullable=True))

    with op.batch_alter_table('bot_modules', schema=None) as batch_op:
        batch_op.alter_column(
            'status', existing_type=sa.Enum(*OLD_MODULE_STATUSES, name='bot_module_status'),
            type_=sa.Enum(*NEW_MODULE_STATUSES, name='bot_module_status'), existing_nullable=False, existing_server_default='pending',
        )


def downgrade():
    # Thu hẹp enum sẽ LỖI (đúng ý) nếu còn module ở 'awaiting_domain_confirmation': người vận hành tự chuyển trạng thái trước khi downgrade.
    with op.batch_alter_table('bot_modules', schema=None) as batch_op:
        batch_op.alter_column(
            'status', existing_type=sa.Enum(*NEW_MODULE_STATUSES, name='bot_module_status'),
            type_=sa.Enum(*OLD_MODULE_STATUSES, name='bot_module_status'), existing_nullable=False, existing_server_default='pending',
        )

    with op.batch_alter_table('module_urls', schema=None) as batch_op:
        batch_op.drop_column('extracted_at')
        batch_op.drop_column('updated_at')
        batch_op.drop_column('created_at')
        batch_op.drop_column('upload_status')
        batch_op.drop_column('entry_html_filename')
        batch_op.drop_column('upload_storage_key')
        batch_op.drop_column('detected_domain')
        batch_op.drop_column('source_url')
        batch_op.add_column(sa.Column('raw_html_storage_key', sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column('crawled_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('domain', sa.String(length=255), server_default='', nullable=False))
        batch_op.add_column(sa.Column('url', sa.String(length=2048), server_default='', nullable=False))
        batch_op.alter_column('url', server_default=None)
