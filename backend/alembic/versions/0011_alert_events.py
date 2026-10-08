"""Record each alert pop-up, for cooldowns and the count of alerts per day."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011_alert_events"
down_revision: str | Sequence[str] | None = "0010_managers"


def upgrade() -> None:
    op.create_table(
        "alert_events",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("store_id", sa.String(32), nullable=False),
        sa.Column(
            "manager_id",
            sa.String(32),
            sa.ForeignKey("app.managers.manager_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "code",
            sa.String(48),
            sa.ForeignKey("app.preference_definitions.code", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("value", sa.Numeric(), nullable=False),
        sa.Column("threshold", sa.Numeric(), nullable=False),
        sa.Column("snapshot_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "triggered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "details",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(details) = 'object'",
            name="ck_alert_events_details",
        ),
        schema="app",
    )
    op.create_index(
        "ix_alert_events_manager_code_time",
        "alert_events",
        ["store_id", "manager_id", "code", "triggered_at"],
        schema="app",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_alert_events_manager_code_time",
        table_name="alert_events",
        schema="app",
    )
    op.drop_table("alert_events", schema="app")
