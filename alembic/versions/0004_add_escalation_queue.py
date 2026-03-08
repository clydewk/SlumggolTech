"""Add escalation queue table."""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0004_escalation_queue"
down_revision = "0003_claim_intel_cache_cols"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "escalation_queue",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.String(128), nullable=False),
        sa.Column("message_id", sa.String(128), nullable=False),
        sa.Column("claim_key", sa.String(128), nullable=True),
        sa.Column("canonical_claim_en", sa.Text, nullable=False),
        sa.Column("verdict", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("evidence_json", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("reviewer_note", sa.Text, nullable=True),
        sa.Column("corrected_reply", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_escalation_queue_group_id",
        "escalation_queue",
        ["group_id"],
    )
    op.create_index(
        "ix_escalation_queue_message_id",
        "escalation_queue",
        ["message_id"],
    )
    op.create_index(
        "ix_escalation_queue_status",
        "escalation_queue",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_escalation_queue_status", table_name="escalation_queue")
    op.drop_index("ix_escalation_queue_message_id", table_name="escalation_queue")
    op.drop_index("ix_escalation_queue_group_id", table_name="escalation_queue")
    op.drop_table("escalation_queue")
