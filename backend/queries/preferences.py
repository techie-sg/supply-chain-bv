"""Read the preference catalogue and store each manager's preference values."""

from sqlalchemy import Engine, Executable, select, update
from sqlalchemy.orm import Session

from database.models import PreferenceDefinition, StorePreference
from database.session import get_session
from domain.memory import PreferenceStatus


def list_definitions(engine: Engine | None = None) -> list[PreferenceDefinition]:
    with get_session(engine) as session:
        return list(session.scalars(select(PreferenceDefinition)))


def active_preferences(
    store_id: str,
    manager_id: str,
    engine: Engine | None = None,
) -> list[StorePreference]:
    statement = select(StorePreference).where(
        StorePreference.store_id == store_id,
        StorePreference.manager_id == manager_id,
        StorePreference.status == PreferenceStatus.ACTIVE,
    )
    with get_session(engine) as session:
        return list(session.scalars(statement))


def _retire_active(
    store_id: str,
    manager_id: str,
    code: str,
    status: PreferenceStatus,
) -> Executable:
    return (
        update(StorePreference)
        .where(
            StorePreference.store_id == store_id,
            StorePreference.manager_id == manager_id,
            StorePreference.code == code,
            StorePreference.status == PreferenceStatus.ACTIVE,
        )
        .values(status=status)
        .returning(StorePreference.id)
    )


def save_preference(
    store_id: str,
    manager_id: str,
    code: str,
    enabled: bool,
    value: object | None,
    options: dict | None,
    engine: Engine | None = None,
) -> StorePreference:
    """Supersede the active value, if any, and store the new one in one transaction."""
    with get_session(engine) as session:
        return persist_preference(
            session,
            store_id,
            manager_id,
            code,
            enabled,
            value,
            options,
        )


def persist_preference(
    session: Session,
    store_id: str,
    manager_id: str,
    code: str,
    enabled: bool,
    value: object | None,
    options: dict | None,
) -> StorePreference:
    """Write within a caller-owned query transaction, including suggestion acceptance."""
    preference = StorePreference(
        store_id=store_id,
        manager_id=manager_id,
        code=code,
        enabled=enabled,
        value=value,
        options=options,
        status=PreferenceStatus.ACTIVE,
    )
    session.execute(
        _retire_active(store_id, manager_id, code, PreferenceStatus.SUPERSEDED),
    )
    session.add(preference)
    session.flush()
    session.refresh(preference)
    return preference


def remove_preference(
    store_id: str,
    manager_id: str,
    code: str,
    engine: Engine | None = None,
) -> bool:
    """Mark the active value removed so the default applies; False if none existed."""
    with get_session(engine) as session:
        removed = session.execute(
            _retire_active(store_id, manager_id, code, PreferenceStatus.REMOVED),
        ).all()
    return bool(removed)
