"""Initial Postgres schema."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "groups",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=False, unique=True),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("analysis_mode", sa.String(length=32), nullable=False, server_default="gated"),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("style_profile", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_groups_external_id", "groups", ["external_id"], unique=True)

    op.create_table(
        "claim_cache_entries",
        sa.Column("claim_key", sa.String(length=128), primary_key=True, nullable=False),
        sa.Column("verdict", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("reply_language", sa.String(length=32), nullable=False),
        sa.Column("reply_template", sa.Text(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("source_quality_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "hot_claim_entries",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("hash_key", sa.String(length=128), nullable=False),
        sa.Column("claim_key", sa.String(length=128), nullable=True),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_hot_claim_entries_hash_key", "hot_claim_entries", ["hash_key"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_hot_claim_entries_hash_key", table_name="hot_claim_entries")
    op.drop_table("hot_claim_entries")
    op.drop_table("claim_cache_entries")
    op.drop_index("ix_groups_external_id", table_name="groups")
    op.drop_table("groups")
