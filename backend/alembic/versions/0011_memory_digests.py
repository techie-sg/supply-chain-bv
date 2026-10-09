"""Add the memory digest: what the daily review remembers per manager."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0011_memory_digests"
down_revision: str | Sequence[str] | None = "0010_managers"


def upgrade() -> None:
    op.create_table(
        "memory_digests",
        sa.Column("manager_id", sa.String(32), nullable=False),
        sa.Column("store_id", sa.String(32), nullable=False),
        sa.Column("digest", sa.Text(), nullable=True),
        sa.Column(
            "sources",
            JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "built_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("manager_id"),
        sa.ForeignKeyConstraint(
            ["manager_id"],
            ["app.managers.manager_id"],
            name="fk_memory_digests_manager",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(sources) = 'array'",
            name="ck_memory_digests_sources",
        ),
        schema="app",
    )


def downgrade() -> None:
    op.drop_table("memory_digests", schema="app")
