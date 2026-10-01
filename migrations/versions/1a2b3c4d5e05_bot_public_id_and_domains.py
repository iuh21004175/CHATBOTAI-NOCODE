"""bot public_id + bảng bot_domains (nhiều domain được nhúng widget cho 1 bot)

Revision ID: 1a2b3c4d5e05
Revises: 1a2b3c4d5e04
Create Date: 2026-09-24 10:00:00.000000

- bots.public_id: định danh công khai ngẫu nhiên dùng ở mã nhúng/API widget thay cho id tuần tự. Bot đã có được backfill.
  Mã nhúng cũ (data-bot-id="<id số>") sẽ không còn dùng được — chủ website phải lấy mã mới ở Bước 3 (Xuất bản).
- bot_domains: mỗi bot nhiều domain. Backfill từ bot_settings.widget_domain (dạng đã chuẩn hóa). Cột widget_domain được GIỮ
  (không còn được đọc) để rollback không mất dữ liệu.
"""
import secrets
from urllib.parse import urlparse

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1a2b3c4d5e05'
down_revision = '1a2b3c4d5e04'
branch_labels = None
depends_on = None


def _normalize_domain(domain):
    """Cùng quy tắc app/widget/domains.py:normalize_domain (migration tự chứa, không import mã ứng dụng)."""
    value = (domain or "").strip().lower()
    if "://" in value:
        value = urlparse(value).hostname or ""
    value = value.split("/")[0].split(":")[0]
    return value.removeprefix("www.")


def upgrade():
    bind = op.get_bind()

    # ---- bots.public_id: thêm cột cho phép NULL -> backfill -> đổi NOT NULL + chỉ mục duy nhất ----
    with op.batch_alter_table('bots', schema=None) as batch_op:
        batch_op.add_column(sa.Column('public_id', sa.String(length=64), nullable=True))
    bot_ids = [row[0] for row in bind.execute(sa.text("SELECT id FROM bots")).fetchall()]
    for bot_id in bot_ids:
        bind.execute(sa.text("UPDATE bots SET public_id = :p WHERE id = :i"), {"p": secrets.token_urlsafe(24), "i": bot_id})
    with op.batch_alter_table('bots', schema=None) as batch_op:
        batch_op.alter_column('public_id', existing_type=sa.String(length=64), nullable=False)
        batch_op.create_index(batch_op.f('ix_bots_public_id'), ['public_id'], unique=True)

    # ---- bot_domains ----
    op.create_table(
        'bot_domains',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('bot_id', sa.Integer(), nullable=False),
        sa.Column('domain', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['bot_id'], ['bots.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('bot_id', 'domain', name='uq_bot_domain'),
    )
    with op.batch_alter_table('bot_domains', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_bot_domains_bot_id'), ['bot_id'], unique=False)

    rows = bind.execute(sa.text(
        "SELECT bot_id, widget_domain FROM bot_settings WHERE widget_domain IS NOT NULL AND widget_domain <> ''"
    )).fetchall()
    for bot_id, raw in rows:
        domain = _normalize_domain(raw)
        if domain:
            bind.execute(
                sa.text("INSERT INTO bot_domains (bot_id, domain, created_at) VALUES (:b, :d, NOW())"),
                {"b": bot_id, "d": domain},
            )


def downgrade():
    op.drop_table('bot_domains')
    with op.batch_alter_table('bots', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_bots_public_id'))
        batch_op.drop_column('public_id')
