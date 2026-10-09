"""Postgres connection and transactional sessions, adapted from Agenttil."""

from collections.abc import Iterator
from contextlib import contextmanager
from threading import RLock

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session

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
        # Reuse connections across callbacks. A bounded pool avoids repeating
        # authentication/TLS for every query while limiting database usage.
        pool_size=5,
        max_overflow=5,
        pool_timeout=5,
        pool_pre_ping=True,
        pool_recycle=1800,
        hide_parameters=True,
        connect_args={
            "connect_timeout": 5,
            "options": "-c statement_timeout=5000",
            "prepare_threshold": None,
        },
    )


class DatabaseResources:
    """The app/job owner creates its engine once and disposes it on exit."""

    def __init__(self) -> None:
        self._engine: Engine | None = None
        self._lock = RLock()

    def engine(self) -> Engine:
        with self._lock:
            if self._engine is None:
                self._engine = build_engine()
            return self._engine

    def close(self) -> None:
        with self._lock:
            if self._engine is not None:
                self._engine.dispose()
                self._engine = None


_resources: DatabaseResources | None = None


@contextmanager
def database_resources() -> Iterator[DatabaseResources]:
    """Composition boundary shared by app callbacks and one-shot jobs.

    Gradio serves callbacks on other threads, so the owner is process-wide.
    Nested entry points reuse it. Explicit query engines always take precedence.
    """
    global _resources
    if _resources is not None:
        yield _resources
        return
    owner = DatabaseResources()
    _resources = owner
    try:
        yield owner
    finally:
        _resources = None
        owner.close()


@contextmanager
def get_session(engine: Engine | None = None) -> Iterator[Session]:
    """Commit on success, roll back on failure, and close the connection."""
    own_engine = engine is None and _resources is None
    engine = (
        engine
        if engine is not None
        else (_resources.engine() if _resources else build_engine())
    )
    try:
        with Session(engine, expire_on_commit=False) as session, session.begin():
            yield session
    finally:
        if own_engine:
            engine.dispose()
