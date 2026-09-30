"""Validate an entire scenario before replacing operational rows."""

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ScenarioRow(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ZoneData(ScenarioRow):
    zone_id: str
    zone_name: str
    distance_from_store_km: float = Field(ge=0)
    avg_ride_min_dry: float = Field(ge=0)
    avg_ride_min_rain: float = Field(ge=0)


class RiderData(ScenarioRow):
    rider_id: str
    name: str
    status: str
    current_zone: str
    hours_on_shift: float = Field(ge=0)
    deliveries_today: int = Field(ge=0)
    minutes_since_last_break: int = Field(ge=0)
    eta_back_min: float | None = Field(default=None, ge=0)
    employment_type: str


class OrderData(ScenarioRow):
    order_id: str
    zone_id: str
    item_count: int = Field(gt=0)
    has_frozen_items: bool
    status: str
    assigned_rider_id: str | None
    age_sec: int = Field(ge=0)


class HourlyData(ScenarioRow):
    day_offset: int = Field(ge=1)
    hour: int = Field(ge=0, le=23)
    orders: int = Field(ge=0)
    avg_pick_pack_min: float = Field(ge=0)
    avg_rider_wait_min: float = Field(ge=0)
    avg_ride_min: float = Field(ge=0)
    sla_10min_pct: float = Field(ge=0, le=100)
    riders_online: int = Field(ge=0)
    rain_flag: bool


class RouteData(ScenarioRow):
    order_ids: list[str] = Field(min_length=2)
    same_or_adjacent_zones: bool
    added_detour_km: float = Field(ge=0)
    added_detour_min: float = Field(ge=0)
    include_eta: bool = False


class ScenarioData(ScenarioRow):
    title: str
    store_id: str
    is_raining: bool
    zones: list[ZoneData] = Field(min_length=1)
    hourly_metrics: list[HourlyData]
    riders: list[RiderData]
    orders: list[OrderData]
    rain_started_minutes_ago: int | None = Field(default=None, ge=0)
    candidate_routes: list[RouteData] = Field(default_factory=list)
    route_source: str | None = None
    declared_surge: bool | None = None
    three_order_batch_approved: bool | None = None

    @model_validator(mode="after")
    def check_references(self):
        zone_ids = [zone.zone_id for zone in self.zones]
        rider_ids = [rider.rider_id for rider in self.riders]
        order_ids = [order.order_id for order in self.orders]
        hours = [(row.day_offset, row.hour) for row in self.hourly_metrics]
        for name, values in (
            ("zone", zone_ids),
            ("rider", rider_ids),
            ("order", order_ids),
            ("hourly", hours),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate {name} identifier")
        for rider in self.riders:
            if rider.current_zone not in zone_ids:
                raise ValueError(f"unknown zone for rider {rider.rider_id}")
        for order in self.orders:
            if order.zone_id not in zone_ids:
                raise ValueError(f"unknown zone for order {order.order_id}")
            if order.assigned_rider_id and order.assigned_rider_id not in rider_ids:
                raise ValueError(f"unknown rider for order {order.order_id}")
        for route in self.candidate_routes:
            if len(route.order_ids) != len(set(route.order_ids)) or not set(
                route.order_ids
            ) <= set(order_ids):
                raise ValueError("candidate route contains duplicate or unknown orders")
        return self
