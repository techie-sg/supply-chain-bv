"""Load self-contained synthetic dispatch scenarios from YAML."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any

import structlog
import yaml
from pydantic import ValidationError
from sqlalchemy import Engine

from constants import TIMEZONE
from database.models import HourlyMetric, Order, Rider, Zone
from domain.scenario import ScenarioData
from queries.scenarios import read_scenario_rows, replace_scenario
from resources import SCENARIO_DIR

logger = structlog.stdlib.get_logger(__name__)


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


def scenario_metadata(key: str) -> dict[str, Any]:
    """Static fixture details; these are not mutable operational database state."""
    data = _read_scenario(key)
    return {
        "title": data.title,
        "description": data.description,
        "is_raining": data.is_raining,
    }


def scenario_names() -> list[dict[str, str]]:
    scenarios = []
    for key in _scenario_paths():
        data = _read_scenario(key)
        scenarios.append(
            {"key": key, "title": data.title, "description": data.description},
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
    return [*zones, *history, *riders, *orders], context


def load_scenario(key: str, engine: Engine | None = None) -> dict[str, Any]:
    """Atomically replace all operational rows with one validated scenario."""
    started = perf_counter()
    rows, _ = build_scenario(key, datetime.now(TIMEZONE))
    replace_scenario(rows, engine)
    context = current_scenario(engine)
    if context is None:
        raise RuntimeError("No saved scenario after loading")
    logger.info(
        "Scenario loaded",
        scenario=key,
        counts=context["counts"],
        duration_ms=round((perf_counter() - started) * 1000, 2),
    )
    return context


def reload_current_scenario(engine: Engine | None = None) -> dict[str, Any]:
    """Reset the database's active scenario with timestamps based on the current time."""
    context = current_scenario(engine)
    if context is None:
        raise LookupError("No scenario is loaded. Load one from Demo tools first.")
    return load_scenario(context["scenario_key"], engine)


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
