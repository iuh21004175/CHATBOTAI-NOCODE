"""merge module type "service_support" back into "sales_support" with neutral url-role labels

Yêu cầu: 1 loại module duy nhất ("Hỗ trợ bán hàng", key sales_support) với nhãn URL trung tính, phủ đủ:
(1) web bán sản phẩm có thanh toán trực tuyến, (2) web dịch vụ có thanh toán/đặt cọc trực tuyến,
(3) web bán hàng/dịch vụ KHÔNG thanh toán trên website (bỏ trống cart/checkout). Không còn phân biệt
"sản phẩm" hay "dịch vụ" bằng loại module — cùng 1 loại, xem app/modules/service.py:MODULE_TYPE_SEEDS.

Data migration (không đổi schema): 4 role (product_listing/product_detail/cart/checkout) là enum có sẵn,
dùng chung cho mọi loại module từ trước — không cần đổi cột/enum.
1. Đổi nhãn hiển thị của "sales_support" sang trung tính (mọi DB đã seed từ migration 1a2b3c4d5e10 đều còn
   nhãn cũ "sản phẩm"/"giỏ hàng"/"thanh toán": ensure_module_types() chủ động KHÔNG ghi đè loại đã có, nên
   phải sửa bằng migration, không tự sửa được lúc khởi động app).
2. Nếu có "service_support" (loại tạm thời do phiên bản trước tạo): module đã khai báo theo loại đó được
   CHUYỂN sang "sales_support" — an toàn vì 2 loại dùng chung đúng 4 role key, dữ liệu module_urls/module_actions
   giữ nguyên. Sau đó xoá loại "service_support" (không còn ai tham chiếu).

Revision ID: e2f5a7c9b1d3
Revises: d8e1f4a6c9b2
Create Date: 2026-09-28 11:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e2f5a7c9b1d3'
down_revision = 'd8e1f4a6c9b2'
branch_labels = None
depends_on = None

_OLD_LABELS = {
    'product_detail': 'URL trang sản phẩm', 'product_listing': 'URL trang danh sách sản phẩm',
    'cart': 'URL trang giỏ hàng', 'checkout': 'URL trang thanh toán',
}
_NEW_LABELS = {
    'product_detail': 'URL trang sản phẩm / dịch vụ', 'product_listing': 'URL trang danh sách sản phẩm / dịch vụ',
    'cart': 'URL trang giỏ hàng / đặt lịch (nếu có)', 'checkout': 'URL trang thanh toán / đặt cọc (nếu có)',
}
_SALES_DESC_OLD = 'Trợ lý xem thông tin sản phẩm, thêm vào giỏ và hỗ trợ khách đặt hàng ngay trên website của bạn.'
_SALES_DESC_NEW = 'Trợ lý xem thông tin sản phẩm/dịch vụ, hỗ trợ khách đặt hàng, đặt lịch hoặc liên hệ ngay trên website của bạn.'

module_types = sa.table('module_types', sa.column('id', sa.Integer), sa.column('key', sa.String), sa.column('description', sa.Text))
url_roles = sa.table(
    'module_type_url_roles', sa.column('id', sa.Integer), sa.column('module_type_id', sa.Integer),
    sa.column('url_role', sa.String), sa.column('label', sa.String),
)
bot_modules = sa.table('bot_modules', sa.column('id', sa.Integer), sa.column('module_type_id', sa.Integer))


def _type_id(bind, key):
    return bind.execute(sa.select(module_types.c.id).where(module_types.c.key == key)).scalar()


def upgrade():
    bind = op.get_bind()
    sales_id = _type_id(bind, 'sales_support')
    if sales_id is not None:
        for role, label in _NEW_LABELS.items():
            bind.execute(url_roles.update().where(sa.and_(url_roles.c.module_type_id == sales_id, url_roles.c.url_role == role)).values(label=label))
        bind.execute(module_types.update().where(module_types.c.id == sales_id).values(description=_SALES_DESC_NEW))

    service_id = _type_id(bind, 'service_support')
    if service_id is not None:
        if sales_id is not None:
            # Module đã khai báo theo "service_support" trước đây: chuyển sang "sales_support" — cùng 4 role key nên URL/hành động giữ nguyên vẹn.
            bind.execute(bot_modules.update().where(bot_modules.c.module_type_id == service_id).values(module_type_id=sales_id))
        bind.execute(url_roles.delete().where(url_roles.c.module_type_id == service_id))
        bind.execute(module_types.delete().where(module_types.c.id == service_id))


def downgrade():
    # Best-effort: trả nhãn "sales_support" về cũ và tạo lại loại "service_support" (rỗng, không có module nào — không thể tách ngược các module
    # đã gộp ở upgrade() vì không còn dấu vết chúng từng thuộc loại nào).
    bind = op.get_bind()
    sales_id = _type_id(bind, 'sales_support')
    if sales_id is not None:
        for role, label in _OLD_LABELS.items():
            bind.execute(url_roles.update().where(sa.and_(url_roles.c.module_type_id == sales_id, url_roles.c.url_role == role)).values(label=label))
        bind.execute(module_types.update().where(module_types.c.id == sales_id).values(description=_SALES_DESC_OLD))

    if _type_id(bind, 'service_support') is None:
        bind.execute(module_types.insert().values(key='service_support', name='Hỗ trợ tư vấn dịch vụ', is_active=True,
                     description='Trợ lý xem thông tin dịch vụ, hỗ trợ khách đặt lịch/đăng ký tư vấn ngay trên website của bạn (không có giỏ hàng/thanh toán trực tuyến).'))
        service_id = _type_id(bind, 'service_support')
        rows = [
            {'module_type_id': service_id, 'url_role': 'product_detail', 'label': 'URL trang dịch vụ', 'is_required': True, 'display_order': 1},
            {'module_type_id': service_id, 'url_role': 'product_listing', 'label': 'URL trang danh sách dịch vụ', 'is_required': False, 'display_order': 2},
            {'module_type_id': service_id, 'url_role': 'cart', 'label': 'URL trang đặt lịch / đăng ký tư vấn', 'is_required': False, 'display_order': 3},
            {'module_type_id': service_id, 'url_role': 'checkout', 'label': 'URL trang xác nhận / thanh toán trước (nếu có)', 'is_required': False, 'display_order': 4},
        ]
        bind.execute(url_roles.insert(), rows)
