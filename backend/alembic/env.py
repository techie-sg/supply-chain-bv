"""Alembic environment, adapted from Agenttil's Postgres setup."""

from alembic import context
from database import models  # noqa: F401 - registers tables with Base.metadata
from database.session import Base, build_engine, database_url
from logging_config import configure_logging


def run_migrations() -> None:
    if context.is_offline_mode():
        context.configure(
            url=database_url(),
            target_metadata=Base.metadata,
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
            version_table_schema="app",
        )
        with context.begin_transaction():
            context.execute("CREATE SCHEMA IF NOT EXISTS app")
            context.run_migrations()
        return

    configure_logging()
    engine = build_engine()
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=Base.metadata,
                version_table_schema="app",
                include_schemas=True,
                include_name=lambda name, type_, parent_names: (
                    type_ != "schema" or name == "app"
                ),
            )
            with context.begin_transaction():
                context.execute("CREATE SCHEMA IF NOT EXISTS app")
                context.run_migrations()
    finally:
        engine.dispose()


run_migrations()
