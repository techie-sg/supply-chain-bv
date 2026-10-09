"""The store's shift managers; each has a unique shift and their own settings and chats."""

from sqlalchemy import Engine

from constants import DEMO_MANAGER_ID, DEMO_STORE_ID
from domain.managers import ShiftManager
from queries.managers import list_managers


def store_managers(
    store_id: str = DEMO_STORE_ID,
    engine: Engine | None = None,
) -> list[ShiftManager]:
    return [
        ShiftManager(
            row.manager_id,
            row.name,
            row.shift_id,
            row.shift_name,
            row.shift_start,
            row.shift_end,
        )
        for row in list_managers(store_id, engine)
    ]


def choose_manager(
    requested: str | None,
    managers: list[ShiftManager],
) -> ShiftManager | None:
    """The requested manager if they work at the store, else the demo manager.

    Falls back to the first manager, or None when the store has none.
    """
    by_id = {manager.manager_id: manager for manager in managers}
    return (
        by_id.get(requested or "")
        or by_id.get(DEMO_MANAGER_ID)
        or (managers[0] if managers else None)
    )
