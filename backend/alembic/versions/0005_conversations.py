"""Store chat conversations with their messages as a JSON list."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0005_conversations"
down_revision: str | Sequence[str] | None = "0004_drop_match_document_chunks"


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("store_id", sa.String(32), nullable=False),
        sa.Column("manager_id", sa.String(32), nullable=False),
        sa.Column(
            "messages",
            JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("summary_covers_to", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "jsonb_typeof(messages) = 'array'",
            name="ck_conversations_messages_array",
        ),
        schema="app",
    )
    op.create_index(
        "ix_conversations_store_manager_updated",
        "conversations",
        ["store_id", "manager_id", "updated_at"],
        schema="app",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_conversations_store_manager_updated",
        table_name="conversations",
        schema="app",
    )
    op.drop_table("conversations", schema="app")
