"""Durable manager personalization, separate from legacy weekly digests."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0013_personalization"
down_revision = "0012_query_indexes"


def upgrade() -> None:
    op.create_table(
        "manager_personalization",
        sa.Column("manager_id", sa.String(32), nullable=False),
        sa.Column("store_id", sa.String(32), nullable=False),
        sa.Column(
            "preferences",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.PrimaryKeyConstraint("manager_id"),
        sa.ForeignKeyConstraint(
            ["manager_id"],
            ["app.managers.manager_id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(preferences) = 'object'",
            name="ck_personalization_preferences",
        ),
        schema="app",
    )
    op.drop_constraint(
        "ck_suggestions_kind",
        "suggestions",
        schema="app",
        type_="check",
    )
    op.create_check_constraint(
        "ck_suggestions_kind",
        "suggestions",
        "kind IN ('setting', 'handover_draft', 'answer_issue', 'personalization')",
        schema="app",
    )
    # Legacy digests are retained, but never promoted into user-approved preferences.


def downgrade() -> None:
    op.execute("DELETE FROM app.suggestions WHERE kind = 'personalization'")
    op.drop_constraint(
        "ck_suggestions_kind",
        "suggestions",
        schema="app",
        type_="check",
    )
    op.create_check_constraint(
        "ck_suggestions_kind",
        "suggestions",
        "kind IN ('setting', 'handover_draft', 'answer_issue')",
        schema="app",
    )
    op.drop_table("manager_personalization", schema="app")
