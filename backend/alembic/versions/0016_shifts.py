"""Add shifts the manager starts and ends; link handover notes to shifts and chats.

Each existing note gets an ended shift of its own and loses its old `shift`
date. A chat opened by a hand over links to its note. Pending handover drafts
point at days, not shifts, so they are dismissed.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016_shifts"
down_revision: str | Sequence[str] | None = "0015_merge_personalization"


def upgrade() -> None:
    op.create_table(
        "shifts",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("store_id", sa.String(32), nullable=False),
        sa.Column("manager_id", sa.String(32), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["manager_id"],
            ["app.managers.manager_id"],
            name="fk_shifts_manager",
            ondelete="RESTRICT",
        ),
        schema="app",
    )
    op.create_index(
        "uq_shifts_open",
        "shifts",
        ["manager_id"],
        unique=True,
        schema="app",
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.create_index(
        "ix_shifts_store_ended",
        "shifts",
        ["store_id", "ended_at"],
        schema="app",
    )
    # A shift needs a known manager; notes from anyone else cannot be linked.
    op.execute(
        """
        DELETE FROM app.handover_notes AS note
        WHERE NOT EXISTS (
            SELECT 1 FROM app.managers AS manager
            WHERE manager.manager_id = note.manager_id
        )
        """,
    )
    # Each old note becomes an ended shift; reuse the note's id for its shift.
    op.execute(
        """
        INSERT INTO app.shifts (id, store_id, manager_id, started_at, ended_at)
        SELECT id, store_id, manager_id, created_at, created_at
        FROM app.handover_notes
        """,
    )
    op.add_column(
        "handover_notes",
        sa.Column("shift_id", sa.Uuid(), nullable=True),
        schema="app",
    )
    op.execute("UPDATE app.handover_notes SET shift_id = id")
    op.alter_column("handover_notes", "shift_id", nullable=False, schema="app")
    op.create_foreign_key(
        "fk_handover_notes_shift",
        "handover_notes",
        "shifts",
        ["shift_id"],
        ["id"],
        source_schema="app",
        referent_schema="app",
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_handover_notes_shift",
        "handover_notes",
        ["shift_id"],
        schema="app",
    )
    op.drop_index(
        "ix_handover_notes_store_shift",
        table_name="handover_notes",
        schema="app",
    )
    op.drop_column("handover_notes", "shift", schema="app")
    op.add_column(
        "conversations",
        sa.Column("handover_note_id", sa.Uuid(), nullable=True),
        schema="app",
    )
    op.create_foreign_key(
        "fk_conversations_handover_note",
        "conversations",
        "handover_notes",
        ["handover_note_id"],
        ["id"],
        source_schema="app",
        referent_schema="app",
        ondelete="SET NULL",
    )
    op.execute(
        """
        UPDATE app.suggestions SET status = 'dismissed'
        WHERE kind = 'handover_draft' AND status = 'pending'
        """,
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_conversations_handover_note",
        "conversations",
        schema="app",
        type_="foreignkey",
    )
    op.drop_column("conversations", "handover_note_id", schema="app")
    op.add_column(
        "handover_notes",
        sa.Column("shift", sa.Date(), nullable=True),
        schema="app",
    )
    op.execute(
        """
        UPDATE app.handover_notes AS note
        SET shift = (coalesce(shift.ended_at, shift.started_at)
                     AT TIME ZONE 'Asia/Kolkata')::date
        FROM app.shifts AS shift
        WHERE shift.id = note.shift_id
        """,
    )
    op.alter_column("handover_notes", "shift", nullable=False, schema="app")
    op.create_index(
        "ix_handover_notes_store_shift",
        "handover_notes",
        ["store_id", "shift"],
        schema="app",
    )
    op.drop_constraint(
        "uq_handover_notes_shift",
        "handover_notes",
        schema="app",
        type_="unique",
    )
    op.drop_constraint(
        "fk_handover_notes_shift",
        "handover_notes",
        schema="app",
        type_="foreignkey",
    )
    op.drop_column("handover_notes", "shift_id", schema="app")
    op.drop_table("shifts", schema="app")
