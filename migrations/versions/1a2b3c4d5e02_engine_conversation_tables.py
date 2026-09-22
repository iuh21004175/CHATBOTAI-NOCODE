"""context engine: conversation_state, structured_memory, bot_intent_config

Revision ID: 1a2b3c4d5e02
Revises: 1a2b3c4d5e01
Create Date: 2026-09-21 09:01:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1a2b3c4d5e02'
down_revision = '1a2b3c4d5e01'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'conversation_state',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('conversation_id', sa.Integer(), nullable=False),
        sa.Column('bot_id', sa.Integer(), nullable=False),
        sa.Column('current_intent', sa.String(length=100), nullable=True),
        sa.Column('previous_intent', sa.String(length=100), nullable=True),
        sa.Column('intent_confidence', sa.Float(), nullable=True),
        sa.Column('intent_changed', sa.Boolean(), server_default='0', nullable=False),
        sa.Column('slots', sa.JSON(), nullable=True),
        sa.Column('slot_completion', sa.Float(), server_default='1', nullable=False),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('summary_updated_at', sa.DateTime(), nullable=True),
        sa.Column('last_summarized_message_id', sa.Integer(), nullable=True),
        sa.Column('summary_pending', sa.Boolean(), server_default='0', nullable=False),
        sa.Column('clarification_turns_used', sa.Integer(), server_default='0', nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['bot_id'], ['bots.id']),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('conversation_id'),
    )
    with op.batch_alter_table('conversation_state', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_conversation_state_bot_id'), ['bot_id'], unique=False)

    op.create_table(
        'structured_memory',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('bot_id', sa.Integer(), nullable=False),
        sa.Column('conversation_id', sa.Integer(), nullable=False),
        sa.Column('category', sa.Enum('requirement', 'preference', 'entity', 'constraint', 'confirmed_fact', name='memory_category'), nullable=False),
        sa.Column('mem_key', sa.String(length=100), nullable=False),
        sa.Column('value', sa.Text(), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=False),
        sa.Column('source_message_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['bot_id'], ['bots.id']),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('conversation_id', 'category', 'mem_key', name='uq_structured_memory_item'),
    )
    with op.batch_alter_table('structured_memory', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_structured_memory_bot_id'), ['bot_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_structured_memory_conversation_id'), ['conversation_id'], unique=False)

    op.create_table(
        'bot_intent_config',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('bot_id', sa.Integer(), nullable=False),
        sa.Column('intent_name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('required_slots', sa.JSON(), nullable=True),
        sa.Column('optional_slots', sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(['bot_id'], ['bots.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('bot_id', 'intent_name', name='uq_bot_intent_name'),
    )
    with op.batch_alter_table('bot_intent_config', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_bot_intent_config_bot_id'), ['bot_id'], unique=False)


def downgrade():
    op.drop_table('bot_intent_config')
    op.drop_table('structured_memory')
    op.drop_table('conversation_state')
