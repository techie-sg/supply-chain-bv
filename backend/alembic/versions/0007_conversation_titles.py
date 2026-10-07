"""Give each conversation a short title, set after its first answer."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_conversation_titles"
down_revision: str | Sequence[str] | None = "0006_preferences"


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("title", sa.String(120), nullable=True),
        schema="app",
    )


def downgrade() -> None:
    op.drop_column("conversations", "title", schema="app")
