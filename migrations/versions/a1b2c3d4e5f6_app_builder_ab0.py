"""App Builder AB0: builder_apps + builder_app_versions (chỉ THÊM bảng, không đụng dữ liệu cũ).

Revision ID: a1b2c3d4e5f6
Revises: a4c6e8b0d2f1
Create Date: 2026-10-01 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = 'a4c6e8b0d2f1'
branch_labels = None
depends_on = None

STATUSES = ('spec_generating', 'spec_ready', 'generating', 'ready', 'failed')


def upgrade():
    op.create_table(
        'builder_apps',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('team_id', sa.Integer(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('public_id', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('prompt', sa.Text(), nullable=False),
        sa.Column('status', sa.Enum(*STATUSES, name='builder_app_status'), server_default='spec_generating', nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('spec', sa.JSON(), nullable=True),
        sa.Column('db_name', sa.String(length=64), nullable=True),
        sa.Column('storage_quota_mb', sa.Integer(), server_default='500', nullable=False),
        sa.Column('current_version_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id']),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('public_id'),
        sa.UniqueConstraint('db_name'),
    )
    op.create_index(op.f('ix_builder_apps_team_id'), 'builder_apps', ['team_id'], unique=False)
    op.create_table(
        'builder_app_versions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('app_id', sa.Integer(), nullable=False),
        sa.Column('number', sa.Integer(), nullable=False),
        sa.Column('spec', sa.JSON(), nullable=False),
        sa.Column('files', sa.JSON(), nullable=False),
        sa.Column('llm_calls', sa.JSON(), nullable=True),
        sa.Column('llm_cost_vnd', sa.Numeric(14, 4), server_default='0', nullable=False),
        sa.Column('cost_reported', sa.Boolean(), server_default='1', nullable=False),
        sa.Column('scan_warnings', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['app_id'], ['builder_apps.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('app_id', 'number', name='uq_builder_version_number'),
    )
    op.create_index(op.f('ix_builder_app_versions_app_id'), 'builder_app_versions', ['app_id'], unique=False)


def downgrade():
    # Chỉ xoá 2 bảng metadata của builder; database MySQL riêng của từng app (pf_*) KHÔNG bị đụng — người vận hành tự quyết định.
    op.drop_index(op.f('ix_builder_app_versions_app_id'), table_name='builder_app_versions')
    op.drop_table('builder_app_versions')
    op.drop_index(op.f('ix_builder_apps_team_id'), table_name='builder_apps')
    op.drop_table('builder_apps')
