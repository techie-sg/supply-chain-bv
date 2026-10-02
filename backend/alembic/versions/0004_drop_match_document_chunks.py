"""Drop app.match_document_chunks; retrieval queries through SQLAlchemy instead."""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_drop_match_document_chunks"
down_revision: str | Sequence[str] | None = "0003_match_document_chunks"


def upgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS app.match_document_chunks")


def downgrade() -> None:
    # Intentionally empty: the function was unused and is recreated by 0003.
    pass
