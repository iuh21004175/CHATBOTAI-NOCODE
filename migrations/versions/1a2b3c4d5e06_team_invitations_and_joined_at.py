"""lời mời vào team (team_invitations) + ngày tham gia của thành viên (team_members.joined_at)

Revision ID: 1a2b3c4d5e06
Revises: 1a2b3c4d5e05
Create Date: 2026-09-24 14:00:00.000000

Chỉ thêm bảng/cột (không xóa hay sửa dữ liệu có sẵn). joined_at cho phép NULL: thành viên có từ trước không có mốc tham gia.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1a2b3c4d5e06'
down_revision = '1a2b3c4d5e05'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('team_members', schema=None) as batch_op:
        batch_op.add_column(sa.Column('joined_at', sa.DateTime(), nullable=True))

    op.create_table(
        'team_invitations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('team_id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False),
        sa.Column('invited_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('accepted_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['invited_by_user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('team_invitations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_team_invitations_team_id'), ['team_id'], unique=False)


def downgrade():
    op.drop_table('team_invitations')
    with op.batch_alter_table('team_members', schema=None) as batch_op:
        batch_op.drop_column('joined_at')
