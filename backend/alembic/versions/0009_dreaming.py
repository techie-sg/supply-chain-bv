"""Add handover notes, dreaming suggestions, and the dreaming position per chat."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0009_dreaming"
down_revision: str | Sequence[str] | None = "0008_conversation_summarized_at"


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("dreamed_to", sa.Integer(), nullable=True),
        schema="app",
    )
    op.create_table(
        "handover_notes",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("store_id", sa.String(32), nullable=False),
        sa.Column("manager_id", sa.String(32), nullable=False),
        sa.Column("shift", sa.Date(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="app",
    )
    op.create_index(
        "ix_handover_notes_store_shift",
        "handover_notes",
        ["store_id", "shift"],
        schema="app",
    )
    op.create_table(
        "suggestions",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("store_id", sa.String(32), nullable=False),
        sa.Column("manager_id", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "evidence",
            JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(16),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "kind IN ('setting', 'handover_draft', 'answer_issue')",
            name="ck_suggestions_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'dismissed')",
            name="ck_suggestions_status",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object' AND jsonb_typeof(evidence) = 'array'",
            name="ck_suggestions_json",
        ),
        schema="app",
    )
    op.create_index(
        "ix_suggestions_manager_status",
        "suggestions",
        ["store_id", "manager_id", "status"],
        schema="app",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_suggestions_manager_status",
        table_name="suggestions",
        schema="app",
    )
    op.drop_table("suggestions", schema="app")
    op.drop_index(
        "ix_handover_notes_store_shift",
        table_name="handover_notes",
        schema="app",
    )
    op.drop_table("handover_notes", schema="app")
    op.drop_column("conversations", "dreamed_to", schema="app")
