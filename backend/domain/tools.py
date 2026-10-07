from pydantic import BaseModel, ConfigDict, Field

# --- inputs ---


class LiveStatusInput(BaseModel):
    model_config = ConfigDict(json_schema_extra={"additionalProperties": False})

    store_id: str = Field(
        min_length=1,
        description="Dark store identifier, as shown for the current scenario.",
    )


class MetricsInput(BaseModel):
    model_config = ConfigDict(json_schema_extra={"additionalProperties": False})

    store_id: str = Field(min_length=1, description="Dark store identifier.")
    date: str = Field(
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Calendar date in Asia/Kolkata, YYYY-MM-DD.",
    )
    start_hour: int = Field(
        ge=0,
        le=23,
        description="First hour of the period, inclusive, 24-hour clock.",
    )
    end_hour: int = Field(
        ge=1,
        le=24,
        description=(
            "End of the period, exclusive, 24-hour clock. "
            "Must be greater than start_hour. 8 to 10pm is start_hour=20, end_hour=22."
        ),
    )


# --- outputs: the source of each tool's MCP outputSchema ---


class Order(BaseModel):
    order_id: str
    status: str
    zone_id: str
    item_count: int
    has_frozen_items: bool
    assigned_rider_id: str | None
    placed_at: str | None
    age_sec: int


class Queue(BaseModel):
    open_orders: int
    packed_waiting: int
    counts_by_status: dict[str, int]
    oldest_order_age_sec: int | None
    oldest_packed_waiting_age_sec: int | None
    oldest_frozen_packed_age_sec: int | None
    orders_truncated: bool
    orders: list[Order]


class Rider(BaseModel):
    rider_id: str
    name: str
    status: str
    current_zone: str | None
    employment_type: str
    hours_on_shift: float
    minutes_since_last_break: int
    deliveries_today: int
    eta_back_min: float | None


class Zone(BaseModel):
    zone_id: str
    zone_name: str
    distance_from_store_km: float
    avg_ride_min_dry: float
    avg_ride_min_rain: float


class LiveSummary(BaseModel):
    available_riders: int
    riders_returning_within_10_min: int
    pending_per_available_rider: float | None
    note: str | None


class LiveDispatchStatus(BaseModel):
    store_id: str
    scenario_key: str
    as_of: str
    data_age_sec: int
    stale: bool
    stale_after_sec: int
    conditions: dict[str, bool]
    queue: Queue
    riders: list[Rider]
    zones: list[Zone]
    summary: LiveSummary


class Period(BaseModel):
    date: str
    start_hour: int
    end_hour: int
    hours_requested: int
    hours_returned: int
    hours_missing: list[int]


class HourlyMetric(BaseModel):
    hour: int
    orders: int
    sla_10min_pct: float
    avg_pick_pack_min: float
    avg_rider_wait_min: float
    avg_ride_min: float
    riders_online: int
    rain_flag: bool


class PeriodSummary(BaseModel):
    total_orders: int
    sla_10min_pct: float | None
    avg_pick_pack_min: float | None
    avg_rider_wait_min: float | None
    avg_ride_min: float | None
    avg_riders_online: float
    orders_per_rider_online: float | None
    rain_hours: int


class DeliveryMetrics(BaseModel):
    store_id: str
    period: Period
    hourly: list[HourlyMetric]
    period_summary: PeriodSummary
    source: str
