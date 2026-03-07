"""Add SimHash columns for cache and hot claims."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002_add_simhash_columns"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "claim_cache_entries",
        sa.Column("canonical_text_simhash", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "hot_claim_entries",
        sa.Column("text_simhash", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("hot_claim_entries", "text_simhash")
    op.drop_column("claim_cache_entries", "canonical_text_simhash")
