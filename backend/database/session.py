"""Postgres connection and transactional sessions, adapted from Agenttil."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session
from sqlalchemy.pool import NullPool

from config import get_settings


class Base(DeclarativeBase):
    pass


def database_url() -> str:
    configured = get_settings().database_url
    if configured is None:
        raise RuntimeError("Set DATABASE_URL or DB_URL before accessing the database")
    return normalize_pg_url(configured.get_secret_value())


def normalize_pg_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def build_engine(url: str | None = None) -> Engine:
    return create_engine(
        normalize_pg_url(url or database_url()),
        poolclass=NullPool,
        hide_parameters=True,
        connect_args={
            "connect_timeout": 5,
            "options": "-c statement_timeout=5000",
            "prepare_threshold": None,
        },
    )


@contextmanager
def get_session(engine: Engine | None = None) -> Iterator[Session]:
    """Commit on success, roll back on failure, and close the connection."""
    own_engine = engine is None
    engine = engine or build_engine()
    try:
        with Session(engine, expire_on_commit=False) as session, session.begin():
            yield session
    finally:
        if own_engine:
            engine.dispose()
