"""Persistence-free ShiftManager contract."""

from dataclasses import dataclass


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
