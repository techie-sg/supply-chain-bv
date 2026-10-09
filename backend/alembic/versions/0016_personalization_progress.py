"""Track each chat's completed personalization batches independently of summaries."""

import sqlalchemy as sa

from alembic import op

revision = "0016_personalization_progress"
down_revision = "0015_merge_personalization"


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("personalization_covers_to", sa.Integer(), nullable=True),
        schema="app",
    )
    op.add_column(
        "conversations",
        sa.Column("personalized_at", sa.DateTime(timezone=True), nullable=True),
        schema="app",
    )
    # Leave existing chats pending: summary coverage cannot prove review succeeded.
    op.create_check_constraint(
        "ck_conversations_personalization_position",
        "conversations",
        "personalization_covers_to IS NULL OR "
        "(personalization_covers_to >= 0 AND "
        "personalization_covers_to < jsonb_array_length(messages))",
        schema="app",
    )
    op.create_index(
        "ix_conversations_pending_personalization",
        "conversations",
        ["created_at"],
        schema="app",
        postgresql_where=sa.text(
            "summary IS NOT NULL AND "
            "coalesce(personalization_covers_to, -1) < summary_covers_to",
        ),
    )


def downgrade() -> None:
    op.drop_index("ix_conversations_pending_personalization", schema="app")
    op.drop_constraint(
        "ck_conversations_personalization_position",
        "conversations",
        schema="app",
        type_="check",
    )
    op.drop_column("conversations", "personalized_at", schema="app")
    op.drop_column("conversations", "personalization_covers_to", schema="app")
