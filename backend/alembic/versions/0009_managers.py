"""Add the store's shift managers, each with a unique shift, and link their data."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_managers"
down_revision: str | Sequence[str] | None = "0008_conversation_summarized_at"

MANAGERS = [
    {
        "manager_id": "ananya",
        "store_id": "DS-BLR-014",
        "name": "Ananya Rao",
        "shift_id": "SHIFT-MOR",
        "shift_name": "Morning",
        "shift_start": "06:00",
        "shift_end": "14:00",
    },
    {
        "manager_id": "karthik",
        "store_id": "DS-BLR-014",
        "name": "Karthik Reddy",
        "shift_id": "SHIFT-EVE",
        "shift_name": "Evening",
        "shift_start": "14:00",
        "shift_end": "22:00",
    },
    {
        "manager_id": "imran",
        "store_id": "DS-BLR-014",
        "name": "Imran Shaikh",
        "shift_id": "SHIFT-NGT",
        "shift_name": "Night",
        "shift_start": "22:00",
        "shift_end": "06:00",
    },
]
TIME = r"^([01][0-9]|2[0-3]):[0-5][0-9]$"


def upgrade() -> None:
    managers = op.create_table(
        "managers",
        sa.Column("manager_id", sa.String(32), primary_key=True),
        sa.Column("store_id", sa.String(32), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("shift_id", sa.String(32), nullable=False),
        sa.Column("shift_name", sa.Text(), nullable=False),
        sa.Column("shift_start", sa.String(5), nullable=False),
        sa.Column("shift_end", sa.String(5), nullable=False),
        sa.UniqueConstraint("shift_id", name="uq_managers_shift_id"),
        sa.CheckConstraint(f"shift_start ~ '{TIME}'", name="ck_managers_shift_start"),
        sa.CheckConstraint(f"shift_end ~ '{TIME}'", name="ck_managers_shift_end"),
        schema="app",
    )
    op.create_index("ix_managers_store", "managers", ["store_id"], schema="app")
    op.bulk_insert(managers, MANAGERS)
    for table in ("conversations", "store_preferences"):
        op.create_foreign_key(
            f"fk_{table}_manager",
            table,
            "managers",
            ["manager_id"],
            ["manager_id"],
            source_schema="app",
            referent_schema="app",
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    for table in ("conversations", "store_preferences"):
        op.drop_constraint(
            f"fk_{table}_manager",
            table,
            schema="app",
            type_="foreignkey",
        )
    op.drop_index("ix_managers_store", table_name="managers", schema="app")
    op.drop_table("managers", schema="app")
