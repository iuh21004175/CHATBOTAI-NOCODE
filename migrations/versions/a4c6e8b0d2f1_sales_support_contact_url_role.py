"""module "sales_support": thêm vai trò URL "trang liên hệ" (tuỳ chọn)

Công ty/cửa hàng không thanh toán trên website mà để khách liên hệ rồi thoả thuận/ký hợp đồng cần khai báo được trang liên hệ.
Data migration (không đổi schema): dùng role "other" có sẵn của enum module_url_role; ensure_module_types() chủ động KHÔNG sửa loại đã có
nên DB đã seed phải thêm dòng này bằng migration (cùng nội dung app/modules/service.py:MODULE_TYPE_SEEDS).

Revision ID: a4c6e8b0d2f1
Revises: d7f9b1c3e5a7
Create Date: 2026-10-01 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a4c6e8b0d2f1'
down_revision = 'd7f9b1c3e5a7'
branch_labels = None
depends_on = None

_LABEL = 'URL trang liên hệ (nếu có)'

module_types = sa.table('module_types', sa.column('id', sa.Integer), sa.column('key', sa.String))
url_roles = sa.table(
    'module_type_url_roles', sa.column('id', sa.Integer), sa.column('module_type_id', sa.Integer), sa.column('url_role', sa.String),
    sa.column('label', sa.String), sa.column('is_required', sa.Boolean), sa.column('display_order', sa.Integer),
)


def _sales_id(bind):
    return bind.execute(sa.select(module_types.c.id).where(module_types.c.key == 'sales_support')).scalar()


def upgrade():
    bind = op.get_bind()
    sales_id = _sales_id(bind)
    if sales_id is None:
        return
    exists = bind.execute(sa.select(url_roles.c.id).where(sa.and_(url_roles.c.module_type_id == sales_id, url_roles.c.url_role == 'other'))).first()
    if exists is None:
        bind.execute(url_roles.insert().values(module_type_id=sales_id, url_role='other', label=_LABEL, is_required=False, display_order=5))


def downgrade():
    bind = op.get_bind()
    sales_id = _sales_id(bind)
    if sales_id is not None:
        bind.execute(url_roles.delete().where(sa.and_(url_roles.c.module_type_id == sales_id, url_roles.c.url_role == 'other')))
