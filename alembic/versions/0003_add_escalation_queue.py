"""add escalation queue

Revision ID: 0003
Revises: 0002
Create Date: 2026-03-08

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'escalation_queue',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('group_id', sa.String(128), nullable=False, index=True),
        sa.Column('message_id', sa.String(128), nullable=False, index=True),
        sa.Column('claim_key', sa.String(128), nullable=True),
        sa.Column('canonical_claim_en', sa.Text, nullable=False),
        sa.Column('verdict', sa.String(32), nullable=False),
        sa.Column('confidence', sa.Float, nullable=False),
        sa.Column('evidence_json', sa.JSON, nullable=False, server_default='[]'),
        sa.Column('status', sa.String(32), nullable=False, server_default='pending', index=True),
        sa.Column('reviewer_note', sa.Text, nullable=True),
        sa.Column('corrected_reply', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_t    op.drop_t    oueue')
