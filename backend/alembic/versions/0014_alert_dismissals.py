"""Remember when a manager closed an alert pop-up, so it stays closed."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014_alert_dismissals"
down_revision: str | Sequence[str] | None = "0013_alert_events"


def upgrade() -> None:
    op.add_column(
        "alert_events",
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        schema="app",
    )


def downgrade() -> None:
    op.drop_column("alert_events", "dismissed_at", schema="app")
