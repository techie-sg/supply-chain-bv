"""Record when each conversation's summary was last updated."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_conversation_summarized_at"
down_revision: str | Sequence[str] | None = "0007_conversation_titles"


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("summarized_at", sa.DateTime(timezone=True), nullable=True),
        schema="app",
    )


def downgrade() -> None:
    op.drop_column("conversations", "summarized_at", schema="app")
