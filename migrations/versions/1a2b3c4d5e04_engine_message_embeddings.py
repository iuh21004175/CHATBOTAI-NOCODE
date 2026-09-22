"""context engine: conversation_message_embeddings (Historical Retrieval, Phase 5)

Revision ID: 1a2b3c4d5e04
Revises: 1a2b3c4d5e03
Create Date: 2026-09-21 09:03:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1a2b3c4d5e04'
down_revision = '1a2b3c4d5e03'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'conversation_message_embeddings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('message_id', sa.Integer(), nullable=False),
        sa.Column('bot_id', sa.Integer(), nullable=False),
        sa.Column('conversation_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['bot_id'], ['bots.id']),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id']),
        sa.ForeignKeyConstraint(['message_id'], ['messages.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('message_id'),
    )
    with op.batch_alter_table('conversation_message_embeddings', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_conversation_message_embeddings_bot_id'), ['bot_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_conversation_message_embeddings_conversation_id'), ['conversation_id'], unique=False)


def downgrade():
    op.drop_table('conversation_message_embeddings')
