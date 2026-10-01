"""Default app.documents.id in the database so REST inserts get a UUID."""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_documents_id_default"
down_revision: str | Sequence[str] | None = "0001_six_tables"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE app.documents ALTER COLUMN id SET DEFAULT gen_random_uuid()",
    )


def downgrade() -> None:
    op.execute("ALTER TABLE app.documents ALTER COLUMN id DROP DEFAULT")
