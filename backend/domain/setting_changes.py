"""Persistence-free SettingChange contract."""

from dataclasses import asdict, dataclass
from typing import Any

from constants import DEMO_MANAGER_ID


@dataclass(frozen=True)
class SettingChange:
    """A validated change waiting for the manager's confirmation."""

    code: str
    name: str
    action: str
    enabled: bool
    value: Any
    options: dict[str, Any] | None
    before: str
    after: str
    # The setting when proposed; a different setting at confirm time means it
    # changed in the meantime, so the proposal is stale.
    proposed_over: list[Any]
    # Whose setting this is; only that manager can confirm it.
    manager_id: str = DEMO_MANAGER_ID

    def to_state(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> "SettingChange":
        return cls(**state)
