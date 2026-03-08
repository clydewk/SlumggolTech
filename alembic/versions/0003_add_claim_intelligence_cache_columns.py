"""Add claim intelligence columns to claim cache entries."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0003_claim_intel_cache_cols"
down_revision = "0002_add_simhash_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "claim_cache_entries",
        sa.Column(
            "claim_category",
            sa.String(length=32),
            nullable=False,
            server_default="other",
        ),
    )
    op.add_column(
        "claim_cache_entries",
        sa.Column(
            "risk_level",
            sa.String(length=32),
            nullable=False,
            server_default="low",
        ),
    )
    op.add_column(
        "claim_cache_entries",
        sa.Column(
            "actionability",
            sa.String(length=32),
            nullable=False,
            server_default="monitor",
        ),
    )
    op.add_column(
        "claim_cache_entries",
        sa.Column(
            "has_official_sg_source",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "claim_cache_entries",
        sa.Column(
            "official_source_domain_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("claim_cache_entries", "official_source_domain_count")
    op.drop_column("claim_cache_entries", "has_official_sg_source")
    op.drop_column("claim_cache_entries", "actionability")
    op.drop_column("claim_cache_entries", "risk_level")
    op.drop_column("claim_cache_entries", "claim_category")
