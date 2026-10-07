"""The MCP server exposes both tools with spec-shaped results and errors."""

import asyncio
import json
from typing import Any

from mcp.client import Client

from mcp_server import server as server_mod

METRICS = {
    "store_id": "S-1",
    "period": {
        "date": "2026-10-03",
        "start_hour": 20,
        "end_hour": 22,
        "hours_requested": 2,
        "hours_returned": 1,
        "hours_missing": [21],
    },
    "hourly": [
        {
            "hour": 20,
            "orders": 10,
            "sla_10min_pct": 80.0,
            "avg_pick_pack_min": 2.5,
            "avg_rider_wait_min": 1.0,
            "avg_ride_min": 4.0,
            "riders_online": 5,
            "rain_flag": False,
        },
    ],
    "period_summary": {
        "total_orders": 10,
        "sla_10min_pct": 80.0,
        "avg_pick_pack_min": 2.5,
        "avg_rider_wait_min": 1.0,
        "avg_ride_min": 4.0,
        "avg_riders_online": 5.0,
        "orders_per_rider_online": 2.0,
        "rain_hours": 0,
    },
    "source": "hourly_metrics (historical aggregates)",
}
ARGS = {"store_id": "S-1", "date": "2026-10-03", "start_hour": 20, "end_hour": 22}


def call(name: str, arguments: dict[str, Any]):
    async def run():
        async with Client(server_mod.server) as client:
            return await client.call_tool(name, arguments)

    return asyncio.run(run())


def test_lists_both_read_only_tools_with_output_schemas() -> None:
    async def run():
        async with Client(server_mod.server) as client:
            return (await client.list_tools()).tools

    tools = {tool.name: tool for tool in asyncio.run(run())}
    assert set(tools) == {"get_live_dispatch_status", "get_delivery_metrics"}
    for tool in tools.values():
        assert tool.annotations.read_only_hint is True
        assert tool.output_schema is not None
    assert tools["get_delivery_metrics"].input_schema["required"] == list(ARGS)


def test_success_returns_structured_content(monkeypatch) -> None:
    monkeypatch.setattr(server_mod.tools, "get_delivery_metrics", lambda **_: METRICS)
    result = call("get_delivery_metrics", ARGS)
    assert not result.is_error
    assert result.structured_content["period_summary"]["total_orders"] == 10
    assert json.loads(result.content[0].text) == METRICS


def test_tool_error_is_a_result_with_the_error_json(monkeypatch) -> None:
    error = {"error": {"code": "UNKNOWN_STORE", "message": "no", "details": {}}}
    monkeypatch.setattr(server_mod.tools, "get_live_dispatch_status", lambda **_: error)
    result = call("get_live_dispatch_status", {"store_id": "X"})
    assert result.is_error
    assert json.loads(result.content[0].text) == error


def test_crash_becomes_data_unavailable(monkeypatch) -> None:
    def boom(**_: Any):
        raise RuntimeError("password=secret")

    monkeypatch.setattr(server_mod.tools, "get_live_dispatch_status", boom)
    result = call("get_live_dispatch_status", {"store_id": "S-1"})
    body = json.loads(result.content[0].text)["error"]
    assert result.is_error
    assert body["code"] == "DATA_UNAVAILABLE"
    assert "secret" not in result.content[0].text


def test_invalid_arguments_never_reach_the_tool(monkeypatch) -> None:
    def fail(**_: Any):
        raise AssertionError("tool should not run")

    monkeypatch.setattr(server_mod.tools, "get_delivery_metrics", fail)
    result = call("get_delivery_metrics", {**ARGS, "end_hour": 0})
    assert result.is_error
