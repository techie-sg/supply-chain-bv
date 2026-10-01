"""Load self-contained synthetic dispatch scenarios from YAML."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from pydantic import ValidationError
from sqlalchemy import Engine

from database.models import HourlyMetric, Order, Rider, Zone
from domain.scenario import ScenarioData
from queries.scenarios import read_scenario_rows, replace_scenario

SCENARIO_DIR = Path(__file__).resolve().parent / "scenario_data"
TIMEZONE = ZoneInfo("Asia/Kolkata")


def _scenario_paths() -> dict[str, Path]:
    return {path.stem: path for path in sorted(SCENARIO_DIR.glob("*.yaml"))}


def _read_scenario(key: str) -> ScenarioData:
    path = _scenario_paths().get(key)
    if path is None:
        raise KeyError(key)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return ScenarioData.model_validate(data)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise ValueError(f"Invalid scenario file: {key}") from exc


def scenario_names() -> list[dict[str, str]]:
    scenarios = []
    for key in _scenario_paths():
        data = _read_scenario(key)
        scenarios.append(
            {"key": key, "title": data.title, "description": data.description}
        )
    return scenarios


def build_scenario(key: str, as_of: datetime) -> tuple[list[object], dict[str, Any]]:
    """Validate and build all rows before touching the database."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must include a timezone")
    data = _read_scenario(key)
    as_of = as_of.astimezone(TIMEZONE).replace(microsecond=0)
    zones = [Zone(**zone.model_dump()) for zone in data.zones]
    history = [
        HourlyMetric(
            store_id=data.store_id,
            date=as_of.date() - timedelta(days=item.day_offset),
            **item.model_dump(exclude={"day_offset"}),
        )
        for item in data.hourly_metrics
    ]
    riders = [
        Rider(
            scenario_key=key,
            store_id=data.store_id,
            as_of=as_of,
            **item.model_dump(),
        )
        for item in data.riders
    ]
    orders = [
        Order(
            scenario_key=key,
            store_id=data.store_id,
            as_of=as_of,
            placed_at=as_of - timedelta(seconds=item.age_sec),
            **item.model_dump(exclude={"age_sec"}),
        )
        for item in data.orders
    ]
    context: dict[str, Any] = {
        "scenario_key": key,
        "title": data.title,
        "as_of": as_of.isoformat(),
        "timezone": "Asia/Kolkata",
        "store_id": data.store_id,
        "is_raining": data.is_raining,
        "counts": {
            "orders": len(orders),
            "riders": len(riders),
            "hourly_metrics": len(history),
            "zones": len(zones),
            "packed_waiting": sum(
                order.status == "packed_waiting_rider" for order in orders
            ),
            "available_riders": sum(rider.status == "available" for rider in riders),
        },
    }
    if data.rain_started_minutes_ago is not None:
        context["rain_started_at"] = (
            as_of - timedelta(minutes=data.rain_started_minutes_ago)
        ).isoformat()
    if data.candidate_routes:
        zone_by_order = {order.order_id: order.zone_id for order in orders}
        rain_ride_by_zone = {zone.zone_id: zone.avg_ride_min_rain for zone in zones}
        routes = []
        for item in data.candidate_routes:
            route = item.model_dump(exclude={"include_eta"})
            if item.include_eta:
                ride_mean = max(
                    rain_ride_by_zone[zone_by_order[order_id]]
                    for order_id in item.order_ids
                )
                route["remaining_delivery_eta_min_range"] = [
                    math.floor(1 + 0.9 * ride_mean + item.added_detour_min),
                    math.ceil(3 + 1.3 * ride_mean + item.added_detour_min),
                ]
            routes.append(route)
        context["candidate_routes"] = routes
        context["route_source"] = data.route_source
        context["eta_method"] = (
            "Illustrative minutes from as_of: floor(1 + 0.9 × rain ride mean + detour) "
            "to ceil(3 + 1.3 × rain ride mean + detour)."
        )
    if data.declared_surge is not None:
        context["declared_surge"] = data.declared_surge
    if data.three_order_batch_approved is not None:
        context["three_order_batch_approved"] = data.three_order_batch_approved
    return [*zones, *history, *riders, *orders], context


def load_scenario(key: str, engine: Engine | None = None) -> dict[str, Any]:
    """Atomically replace all operational rows with one validated scenario."""
    rows, _ = build_scenario(key, datetime.now(TIMEZONE))
    replace_scenario(rows, engine)
    context = current_scenario(engine)
    if context is None:
        raise RuntimeError("No saved scenario after loading")
    return context


def current_scenario(engine: Engine | None = None) -> dict[str, Any] | None:
    """Restore saved rows and their original timestamp for a new UI session."""
    rows = read_scenario_rows(engine)
    orders = [row for row in rows if isinstance(row, Order)]
    riders = [row for row in rows if isinstance(row, Rider)]
    identities = {
        (row.scenario_key, row.store_id, row.as_of) for row in orders + riders
    }
    if not identities:
        return None
    if len(identities) != 1:
        raise ValueError("Saved rows do not describe a single scenario snapshot")
    key, store_id, as_of = identities.pop()
    context: dict[str, Any] = {
        "scenario_key": key,
        "title": key.replace("_", " ").replace("-", " ").title(),
        "as_of": as_of.astimezone(TIMEZONE).isoformat(),
        "timezone": "Asia/Kolkata",
        "store_id": store_id,
        "tables": _scenario_tables(rows),
    }
    context["counts"] = {
        **{name: len(table["data"]) for name, table in context["tables"].items()},
        "packed_waiting": sum(
            order.status == "packed_waiting_rider" for order in orders
        ),
        "available_riders": sum(rider.status == "available" for rider in riders),
    }
    return context


def _scenario_tables(rows: list[object]) -> dict[str, dict[str, Any]]:
    """Serialize snapshot rows for the scenario tables."""
    tables = {}
    for model in (Order, Rider, HourlyMetric, Zone):
        columns = list(model.__table__.columns)
        values = []
        for row in rows:
            if not isinstance(row, model):
                continue
            record = []
            for column in columns:
                value = getattr(row, column.key)
                if isinstance(value, datetime):
                    value = value.astimezone(TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
                elif isinstance(value, date):
                    value = value.isoformat()
                record.append(value)
            values.append(record)
        tables[model.__tablename__] = {
            "headers": [column.name for column in columns],
            "data": values,
        }
    return tables


def scenario_details(key: str) -> dict[str, Any]:
    """Preview any scenario as formatted tables without loading the database."""
    rows, context = build_scenario(key, datetime.now(TIMEZONE))
    return {**context, "tables": _scenario_tables(rows)}
