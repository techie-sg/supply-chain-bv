"""Index pending chat jobs and ordered conversation reads."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012_query_indexes"
down_revision: str | Sequence[str] | None = "0011_memory_digests"


def _create(
    name: str,
    table: str,
    columns: list[str],
    *,
    predicate: str | None = None,
) -> None:
    # Concurrent DDL commits separately. Rebuilding our named index makes a
    # retry safe after an interrupted build, including an invalid partial build.
    op.drop_index(name, schema="app", if_exists=True, postgresql_concurrently=True)
    op.create_index(
        name,
        table,
        columns,
        schema="app",
        postgresql_concurrently=True,
        postgresql_where=sa.text(predicate) if predicate else None,
    )


def upgrade() -> None:
    with op.get_context().autocommit_block():
        # The normal five-second query timeout is unsuitable for building
        # indexes on a large database. This affects only the migration session.
        op.execute("SET statement_timeout = 0")
        try:
            _create(
                "ix_conversations_store_manager_recency",
                "conversations",
                ["store_id", "manager_id", "updated_at", "created_at"],
            )
            _create(
                "ix_conversations_pending_summary",
                "conversations",
                ["created_at"],
                predicate=(
                    "jsonb_array_length(messages) > 0 AND "
                    "coalesce(summary_covers_to, -1) < jsonb_array_length(messages) - 1"
                ),
            )
            _create(
                "ix_conversations_pending_review",
                "conversations",
                ["created_at"],
                predicate="coalesce(dreamed_to, -1) < jsonb_array_length(messages) - 1",
            )
            # Drop superseded indexes only after their replacements are ready.
            op.drop_index(
                "ix_conversations_store_manager_updated",
                schema="app",
                if_exists=True,
                postgresql_concurrently=True,
            )
        finally:
            op.execute("RESET statement_timeout")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("SET statement_timeout = 0")
        try:
            _create(
                "ix_conversations_store_manager_updated",
                "conversations",
                ["store_id", "manager_id", "updated_at"],
            )
            for name in (
                "ix_conversations_store_manager_recency",
                "ix_conversations_pending_summary",
                "ix_conversations_pending_review",
            ):
                op.drop_index(
                    name,
                    schema="app",
                    if_exists=True,
                    postgresql_concurrently=True,
                )
        finally:
            op.execute("RESET statement_timeout")
