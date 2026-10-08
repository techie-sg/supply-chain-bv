"""Read the store's shift managers."""

from sqlalchemy import Engine, select

from database.models import Manager
from database.session import get_session


def list_managers(store_id: str, engine: Engine | None = None) -> list[Manager]:
    """The store's managers, in shift order (by shift start time)."""
    statement = (
        select(Manager)
        .where(Manager.store_id == store_id)
        .order_by(Manager.shift_start, Manager.manager_id)
    )
    with get_session(engine) as session:
        return list(session.scalars(statement))
