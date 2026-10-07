"""DispatchDesk operations tools as a local MCP server over stdio.

Contract: docs/tools.md. Start with `python -m mcp_server.server` from backend/.
Both tools are read-only. stdout carries the protocol, so logs go to stderr.
"""

import json
import sys
from time import perf_counter
from typing import Annotated, Any

import structlog
from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult, ContentBlock, TextContent, ToolAnnotations

from domain.tools import (
    DeliveryMetrics,
    LiveDispatchStatus,
    LiveStatusInput,
    MetricsInput,
)
from logging_config import configure_logging
from service import tools

logger = structlog.stdlib.get_logger(__name__)

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

LIVE_DESCRIPTION = (
    "Get the live order queue, rider statuses, and zone ride times for one "
    "dark store. Use this for any question about what is happening right now: "
    "backlog, which orders are waiting, which riders are free or returning, "
    "batching candidates, ETAs, or a specific rider's hours on shift and time "
    "since last break. Returns figures as of the snapshot time in `as_of`; "
    "always quote that time when stating live numbers. Do not use this for "
    "past performance; use `get_delivery_metrics` instead."
)
METRICS_DESCRIPTION = (
    "Get historical hourly delivery metrics for one dark store on one date "
    "and hour range: orders, 10-minute SLA %, average pick-pack, rider-wait "
    "and ride minutes, riders online, and rain flag, plus a pre-computed "
    "period summary. Use this to explain past performance or compare periods; "
    "call it once per period. The hour range is start-inclusive, end-exclusive: "
    "8 to 10pm is start_hour=20, end_hour=22. Quote the summary figures rather "
    "than recalculating them. Do not use this for the current queue; use "
    "`get_live_dispatch_status` instead."
)

server = MCPServer("dispatchdesk-ops")


def _result(data: dict[str, Any]) -> CallToolResult:
    """Success carries structured content; a tool error carries only the error JSON."""
    text: list[ContentBlock] = [TextContent(type="text", text=json.dumps(data))]
    if "error" in data:
        return CallToolResult(content=text, is_error=True)
    return CallToolResult(content=text, structured_content=data)


def _run(name: str, call: Any, **arguments: Any) -> CallToolResult:
    """Call one tool, audit it, and turn failures into spec error results."""
    started = perf_counter()
    try:
        data = call(**arguments)
    except Exception:
        logger.exception("Tool crashed", tool=name)
        data = {
            "error": {
                "code": "DATA_UNAVAILABLE",
                "message": "Dispatch data could not be read. Retry once; if it "
                "fails again, tell the manager the data cannot be reached.",
                "details": {"retryable": True},
            },
        }
    result = _result(data)
    logger.info(
        "Tool call",
        tool=name,
        arguments=arguments,
        duration_ms=round((perf_counter() - started) * 1000, 2),
        is_error=bool(result.is_error),
    )
    return result


@server.tool(
    title="Live dispatch status",
    description=LIVE_DESCRIPTION,
    annotations=READ_ONLY,
)
def get_live_dispatch_status(
    store_id: Annotated[str, LiveStatusInput.model_fields["store_id"]],
) -> Annotated[CallToolResult, LiveDispatchStatus]:
    return _run(
        "get_live_dispatch_status", tools.get_live_dispatch_status, store_id=store_id
    )


@server.tool(
    title="Delivery metrics",
    description=METRICS_DESCRIPTION,
    annotations=READ_ONLY,
)
def get_delivery_metrics(
    store_id: Annotated[str, MetricsInput.model_fields["store_id"]],
    date: Annotated[str, MetricsInput.model_fields["date"]],
    start_hour: Annotated[int, MetricsInput.model_fields["start_hour"]],
    end_hour: Annotated[int, MetricsInput.model_fields["end_hour"]],
) -> Annotated[CallToolResult, DeliveryMetrics]:
    return _run(
        "get_delivery_metrics",
        tools.get_delivery_metrics,
        store_id=store_id,
        date=date,
        start_hour=start_hour,
        end_hour=end_hour,
    )


if __name__ == "__main__":
    configure_logging(sys.stderr)
    server.run("stdio")
