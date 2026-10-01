"""Website Action Engine (Phase M): module_types, module_type_url_roles, bot_modules, module_urls, module_actions, pending_widget_actions
+ cột allow_agent_payment_actions trên bot_settings + giá trị 'module_analysis_charge' cho credit_transactions.type

Revision ID: 1a2b3c4d5e10
Revises: 1a2b3c4d5e09
Create Date: 2026-09-25 22:00:00.000000

Chỉ THÊM bảng/cột/giá trị enum; cột mới trên bot_settings có server_default (mặc định TẮT) nên bot đang có không đổi hành vi. Seed loại module
đầu tiên 'sales_support' (Hỗ trợ bán hàng). Dữ liệu seed cũng được app/modules/service.py:ensure_module_types tự bổ sung nếu thiếu.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1a2b3c4d5e10'
down_revision = '1a2b3c4d5e09'
branch_labels = None
depends_on = None

URL_ROLES = ('product_listing', 'product_detail', 'cart', 'checkout', 'other')
OLD_CREDIT_TYPES = ('trial_grant', 'execution_charge', 'reserve', 'release', 'topup', 'adjustment')
NEW_CREDIT_TYPES = OLD_CREDIT_TYPES + ('module_analysis_charge',)


def upgrade():
    module_types = op.create_table(
        'module_types',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=50), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='1', nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key'),
    )
    op.create_table(
        'module_type_url_roles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('module_type_id', sa.Integer(), nullable=False),
        sa.Column('url_role', sa.Enum(*URL_ROLES, name='module_url_role'), nullable=False),
        sa.Column('label', sa.String(length=120), nullable=False),
        sa.Column('is_required', sa.Boolean(), server_default='0', nullable=False),
        sa.Column('display_order', sa.Integer(), server_default='0', nullable=False),
        sa.ForeignKeyConstraint(['module_type_id'], ['module_types.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('module_type_id', 'url_role', name='uq_module_type_url_role'),
    )
    with op.batch_alter_table('module_type_url_roles', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_module_type_url_roles_module_type_id'), ['module_type_id'], unique=False)

    op.create_table(
        'bot_modules',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('bot_id', sa.Integer(), nullable=False),
        sa.Column('module_type_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('status', sa.Enum('pending', 'analyzing', 'ready', 'failed', name='bot_module_status'), server_default='pending', nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('last_analyzed_at', sa.DateTime(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('analysis_note', sa.Text(), nullable=True),
        sa.Column('progress_done', sa.Integer(), server_default='0', nullable=False),
        sa.Column('progress_total', sa.Integer(), server_default='0', nullable=False),
        sa.Column('analysis_cost_vnd', sa.Numeric(14, 4), server_default='0', nullable=False),
        sa.Column('analysis_charged_vnd', sa.Numeric(14, 4), server_default='0', nullable=False),
        sa.ForeignKeyConstraint(['bot_id'], ['bots.id']),
        sa.ForeignKeyConstraint(['module_type_id'], ['module_types.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('bot_modules', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_bot_modules_bot_id'), ['bot_id'], unique=False)

    op.create_table(
        'module_urls',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('module_id', sa.Integer(), nullable=False),
        sa.Column('url', sa.String(length=2048), nullable=False),
        sa.Column('domain', sa.String(length=255), server_default='', nullable=False),
        sa.Column('url_role', sa.Enum(*URL_ROLES, name='module_url_role'), server_default='other', nullable=False),
        sa.Column('crawled_at', sa.DateTime(), nullable=True),
        sa.Column('raw_html_storage_key', sa.String(length=500), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['module_id'], ['bot_modules.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('module_urls', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_module_urls_module_id'), ['module_id'], unique=False)

    op.create_table(
        'module_actions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('module_id', sa.Integer(), nullable=False),
        sa.Column('url_id', sa.Integer(), nullable=False),
        sa.Column('action_type', sa.Enum('navigate', 'click', 'fill_form', 'add_to_cart', 'read_info', name='module_action_type'), nullable=False),
        sa.Column('action_name', sa.String(length=80), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('selector_spec', sa.JSON(), nullable=False),
        sa.Column('confidence', sa.Float(), server_default='0', nullable=False),
        sa.Column('risk_level', sa.Enum('read_only', 'cart', 'payment', name='module_risk_level'), server_default='read_only', nullable=False),
        sa.Column('verified', sa.Boolean(), server_default='0', nullable=False),
        sa.Column('failure_reason', sa.String(length=50), nullable=True),
        sa.Column('failed_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['module_id'], ['bot_modules.id']),
        sa.ForeignKeyConstraint(['url_id'], ['module_urls.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('module_id', 'action_name', name='uq_module_action_name'),
    )
    with op.batch_alter_table('module_actions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_module_actions_module_id'), ['module_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_module_actions_url_id'), ['url_id'], unique=False)

    op.create_table(
        'pending_widget_actions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('bot_id', sa.Integer(), nullable=False),
        sa.Column('conversation_id', sa.Integer(), nullable=False),
        sa.Column('action_id', sa.Integer(), nullable=True),
        sa.Column('action_name', sa.String(length=80), nullable=False),
        sa.Column('token', sa.String(length=64), nullable=False),
        sa.Column('params', sa.JSON(), nullable=True),
        sa.Column('requires_confirm', sa.Boolean(), server_default='0', nullable=False),
        sa.Column('status', sa.Enum('pending', 'done', 'failed', name='pending_widget_action_status'), server_default='pending', nullable=False),
        sa.Column('result', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['action_id'], ['module_actions.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['bot_id'], ['bots.id']),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('pending_widget_actions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_pending_widget_actions_action_id'), ['action_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_pending_widget_actions_bot_id'), ['bot_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_pending_widget_actions_conversation_id'), ['conversation_id'], unique=False)

    with op.batch_alter_table('bot_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('allow_agent_payment_actions', sa.Boolean(), server_default='0', nullable=False))

    # Sổ cái: thêm loại giao dịch cho phí phân tích module (thêm CUỐI enum nên các dòng cũ không đổi)
    with op.batch_alter_table('credit_transactions', schema=None) as batch_op:
        batch_op.alter_column(
            'type', existing_type=sa.Enum(*OLD_CREDIT_TYPES, name='credit_transaction_type'),
            type_=sa.Enum(*NEW_CREDIT_TYPES, name='credit_transaction_type'), existing_nullable=False,
        )

    # Seed loại module đầu tiên: "Hỗ trợ bán hàng". Trang chi tiết sản phẩm BẮT BUỘC (nơi có nút thêm giỏ/mua — mọi hành động mua hàng nằm ở đây);
    # trang danh sách, giỏ hàng, thanh toán TUỲ CHỌN.
    op.bulk_insert(module_types, [{
        'id': 1, 'key': 'sales_support', 'name': 'Hỗ trợ bán hàng', 'is_active': True,
        'description': 'Trợ lý xem thông tin sản phẩm, thêm vào giỏ và hỗ trợ khách đặt hàng ngay trên website của bạn.',
    }])
    roles = sa.table(
        'module_type_url_roles', sa.column('module_type_id', sa.Integer), sa.column('url_role', sa.String), sa.column('label', sa.String),
        sa.column('is_required', sa.Boolean), sa.column('display_order', sa.Integer),
    )
    op.bulk_insert(roles, [
        {'module_type_id': 1, 'url_role': 'product_detail', 'label': 'URL trang sản phẩm', 'is_required': True, 'display_order': 1},
        {'module_type_id': 1, 'url_role': 'product_listing', 'label': 'URL trang danh sách sản phẩm', 'is_required': False, 'display_order': 2},
        {'module_type_id': 1, 'url_role': 'cart', 'label': 'URL trang giỏ hàng', 'is_required': False, 'display_order': 3},
        {'module_type_id': 1, 'url_role': 'checkout', 'label': 'URL trang thanh toán', 'is_required': False, 'display_order': 4},
    ])


def downgrade():
    # Thu hẹp enum sẽ LỖI (đúng ý) nếu đã có dòng 'module_analysis_charge': không xoá dòng sổ cái để làm downgrade chạy — người vận hành tự quyết.
    with op.batch_alter_table('credit_transactions', schema=None) as batch_op:
        batch_op.alter_column(
            'type', existing_type=sa.Enum(*NEW_CREDIT_TYPES, name='credit_transaction_type'),
            type_=sa.Enum(*OLD_CREDIT_TYPES, name='credit_transaction_type'), existing_nullable=False,
        )
    with op.batch_alter_table('bot_settings', schema=None) as batch_op:
        batch_op.drop_column('allow_agent_payment_actions')
    op.drop_table('pending_widget_actions')
    op.drop_table('module_actions')
    op.drop_table('module_urls')
    op.drop_table('bot_modules')
    op.drop_table('module_type_url_roles')
    op.drop_table('module_types')
