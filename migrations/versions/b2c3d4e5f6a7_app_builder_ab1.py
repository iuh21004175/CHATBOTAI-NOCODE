"""App Builder AB1: builder_app_members (vai trò app của thành viên nhóm) + builder_app_audit (nhật ký ghi dữ liệu). Chỉ THÊM bảng.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-10-01 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b2c3d4e5f6a7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'builder_app_members',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('app_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('role_id', sa.String(length=40), nullable=False),
        sa.Column('assigned_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['app_id'], ['builder_apps.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['assigned_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('app_id', 'user_id', name='uq_builder_member_app_user'),
    )
    op.create_index(op.f('ix_builder_app_members_app_id'), 'builder_app_members', ['app_id'], unique=False)
    op.create_index(op.f('ix_builder_app_members_user_id'), 'builder_app_members', ['user_id'], unique=False)
    op.create_table(
        'builder_app_audit',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('app_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('action', sa.Enum('create', 'update', 'delete', name='builder_audit_action'), nullable=False),
        sa.Column('collection', sa.String(length=40), nullable=False),
        sa.Column('record_id', sa.BigInteger(), nullable=False),
        sa.Column('old_values', sa.JSON(), nullable=True),
        sa.Column('new_values', sa.JSON(), nullable=True),
        sa.Column('source', sa.Enum('ui', 'chatbot', name='builder_audit_source'), server_default='ui', nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['app_id'], ['builder_apps.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_builder_app_audit_app_id'), 'builder_app_audit', ['app_id'], unique=False)
    op.create_index(op.f('ix_builder_app_audit_created_at'), 'builder_app_audit', ['created_at'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_builder_app_audit_created_at'), table_name='builder_app_audit')
    op.drop_index(op.f('ix_builder_app_audit_app_id'), table_name='builder_app_audit')
    op.drop_table('builder_app_audit')
    op.drop_index(op.f('ix_builder_app_members_user_id'), table_name='builder_app_members')
    op.drop_index(op.f('ix_builder_app_members_app_id'), table_name='builder_app_members')
    op.drop_table('builder_app_members')
