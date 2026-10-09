"""Catalogue and effective setting values exposed by preference services."""

from dataclasses import dataclass
from typing import Any

from domain.memory import AlertOptions


@dataclass(frozen=True)
class SettingDefinition:
    code: str
    category: str
    name: str
    description: str
    value_type: str
    unit: str | None
    operator: str | None
    default_value: Any
    min_value: float | None
    max_value: float | None
    allowed_values: list[str] | None
    default_enabled: bool
    default_cooldown_min: int | None
    locked: bool


@dataclass(frozen=True)
class EffectiveSetting:
    definition: SettingDefinition
    enabled: bool
    value: Any
    options: AlertOptions | None
    customized: bool
