"""Preference catalogue enums and the alert options a manager can set.

The catalogue rows live in app.preference_definitions; these enums mirror their codes.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"


class PreferenceCategory(StrEnum):
    ALERT = "alert"
    BATCHING = "batching"
    INCENTIVE = "incentive"
    BRIEFING = "briefing"


class PreferenceCode(StrEnum):
    RIDER_SHORTAGE_ALERT = "rider_shortage_alert"
    ORDERS_PILING_UP_ALERT = "orders_piling_up_alert"
    ORDER_WAITING_TOO_LONG_ALERT = "order_waiting_too_long_alert"
    FROZEN_ORDER_WAITING_ALERT = "frozen_order_waiting_alert"
    SLA_DIP_ALERT = "sla_dip_alert"
    COLD_CHAIN_ISOLATION = "cold_chain_isolation"
    SURGE_ONLY_BATCHING = "surge_only_batching"
    INCENTIVE_CAP = "incentive_cap"
    BRIEFING = "briefing"


class ValueType(StrEnum):
    NUMBER = "number"
    BOOLEAN = "boolean"
    CHOICE = "choice"
    VIEW_LIST = "view_list"


class Unit(StrEnum):
    ORDERS_PER_RIDER = "orders_per_rider"
    ORDERS = "orders"
    MINUTES = "minutes"
    PERCENT = "percent"
    INR = "inr"


class AlertOperator(StrEnum):
    GT = "gt"
    GTE = "gte"
    LT = "lt"


class BriefingView(StrEnum):
    RIDER_STATS = "rider_stats"
    ORDER_QUEUE = "order_queue"
    OLDEST_ORDER_AGE = "oldest_order_age"
    LAST_HANDOVER_NOTE = "last_handover_note"


class Weekday(StrEnum):
    MON = "mon"
    TUE = "tue"
    WED = "wed"
    THU = "thu"
    FRI = "fri"
    SAT = "sat"
    SUN = "sun"


class PreferenceStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REMOVED = "removed"


class AlertOptions(BaseModel):
    """When an alert applies and how often it may repeat; all optional."""

    model_config = ConfigDict(extra="forbid")

    cooldown_min: int | None = Field(default=None, ge=5, le=240)
    days: list[Weekday] | None = Field(default=None, min_length=1)
    start: str | None = Field(default=None, pattern=TIME_PATTERN)
    end: str | None = Field(default=None, pattern=TIME_PATTERN)

    @field_validator("days")
    @classmethod
    def unique_days(cls, days: list[Weekday] | None) -> list[Weekday] | None:
        if days is not None and len(days) != len(set(days)):
            raise ValueError("days must not repeat")
        return days
