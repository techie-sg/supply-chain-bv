"""The store's shift managers; each has a unique shift and their own settings and chats."""

from dataclasses import dataclass

from sqlalchemy import Engine

from constants import DEMO_MANAGER_ID, DEMO_STORE_ID
from queries.managers import list_managers


@dataclass(frozen=True)
class ShiftManager:
    manager_id: str
    name: str
    shift_id: str
    shift_name: str
    shift_start: str
    shift_end: str

    @property
    def shift(self) -> str:
        """For example "Evening shift, 14:00 to 22:00"."""
        return f"{self.shift_name} shift, {self.shift_start} to {self.shift_end}"


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
