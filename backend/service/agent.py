"""Tool-calling agent loop for DispatchDesk.

This module is the MCP boundary: TOOL_REGISTRY and _dispatch() implement
tool execution locally today. When MCP is added, replace _dispatch() with
session.call_tool(name, args) and TOOL_DEFINITIONS with session.list_tools().
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
import structlog

from config import get_settings, require
from constants import GROQ_MODEL
from domain.chat import ChatMessage
from domain.tools import LiveStatusInput, MetricsInput
from service.tools import get_delivery_metrics, get_live_dispatch_status

logger = structlog.stdlib.get_logger(__name__)

TZ = ZoneInfo("Asia/Kolkata")
MODEL = GROQ_MODEL
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

_SYSTEM_PROMPT = (
    Path(__file__).resolve().parent
    / "rag_data"
    / "prompts"
    / "dispatch_manager_system.md"
).read_text(encoding="utf-8")

# ---------------------------------------------------------------------------
# Tool definitions — sent to the model on every call.
# Descriptions are taken verbatim from docs/tools.md.
# Schemas are generated from the Pydantic input models in domain/tools.py.
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_live_dispatch_status",
            "description": (
                "Get the live order queue, rider statuses, and zone ride times for one "
                "dark store. Use this for any question about what is happening right now: "
                "backlog, which orders are waiting, which riders are free or returning, "
                "batching candidates, ETAs, or a specific rider's hours on shift and time "
                "since last break. Returns figures as of the snapshot time in `as_of`; "
                "always quote that time when stating live numbers. Do not use this for "
                "past performance; use `get_delivery_metrics` instead."
            ),
            "parameters": LiveStatusInput.model_json_schema(),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_delivery_metrics",
            "description": (
                "Get historical hourly delivery metrics for one dark store on one date "
                "and hour range: orders, 10-minute SLA %, average pick-pack, rider-wait "
                "and ride minutes, riders online, and rain flag, plus a pre-computed "
                "period summary. Use this to explain past performance or compare periods; "
                "call it once per period. The hour range is start-inclusive, end-exclusive: "
                "8 to 10pm is start_hour=20, end_hour=22. Quote the summary figures rather "
                "than recalculating them. Do not use this for the current queue; use "
                "`get_live_dispatch_status` instead."
            ),
            "parameters": MetricsInput.model_json_schema(),
        },
    },
]

# Maps tool name → callable. Replace with session.call_tool() when moving to MCP.
TOOL_REGISTRY: dict[str, Any] = {
    "get_live_dispatch_status": get_live_dispatch_status,
    "get_delivery_metrics": get_delivery_metrics,
}


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


class AgentResult(dict):
    """Typed dict returned by run_agent."""


def _make_result(answer: str, trace: list[dict], llm_calls: int) -> AgentResult:
    return AgentResult(answer=answer, trace=trace, llm_calls=llm_calls)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _call_llm(messages: list[dict], tools: list[dict]) -> dict:
    """One raw Groq chat/completions call. Returns the full response dict."""
    api_key = require(get_settings().groq_api_key, "GROQ_API_KEY")
    response = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": MODEL, "messages": messages, "tools": tools, "temperature": 0},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def _dispatch(call: dict) -> str:
    """Execute one tool call and return a JSON string result.

    This is the MCP boundary: swap the body for session.call_tool(name, args)
    when moving to MCP. The signature and return type stay the same.
    """
    name = call["function"]["name"]
    if name not in TOOL_REGISTRY:
        return json.dumps(
            {
                "error": {
                    "code": "UNKNOWN_TOOL",
                    "message": f"No tool named '{name}'.",
                    "details": {"available_tools": sorted(TOOL_REGISTRY)},
                },
            },
        )
    try:
        args = json.loads(call["function"]["arguments"] or "{}")
    except json.JSONDecodeError:
        return json.dumps(
            {
                "error": {
                    "code": "INVALID_INPUT",
                    "message": "Arguments were not valid JSON.",
                    "details": {},
                },
            },
        )
    try:
        result = TOOL_REGISTRY[name](**args)
    except TypeError as exc:
        return json.dumps(
            {
                "error": {"code": "INVALID_INPUT", "message": str(exc), "details": {}},
            },
        )
    return json.dumps(result, default=str)


def _build_messages(
    question: str,
    store_id: str,
    history: list[ChatMessage] | None,
) -> list[dict]:
    today = datetime.now(TZ).date().isoformat()
    system = (
        f"{_SYSTEM_PROMPT}\n\n"
        f"Today's date is {today} (Asia/Kolkata). "
        f"The current store_id is {store_id}."
    )
    messages: list[dict] = [{"role": "system", "content": system}]
    for m in history or []:
        messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": question})
    return messages


def _assistant_turn(message: dict) -> dict:
    turn: dict = {"role": "assistant", "content": message.get("content")}
    if message.get("tool_calls"):
        turn["tool_calls"] = message["tool_calls"]
    return turn


def _tool_turn(call: dict, output: str) -> dict:
    return {"role": "tool", "tool_call_id": call["id"], "content": output}


def _trace_entry(step: int, call: dict, output: str) -> dict:
    parsed = json.loads(output)
    return {
        "step": step,
        "tool": call["function"]["name"],
        "arguments": call["function"]["arguments"],
        "error": parsed.get("error", {}).get("code")
        if isinstance(parsed, dict)
        else None,
        "as_of": parsed.get("as_of") if isinstance(parsed, dict) else None,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_agent(
    question: str,
    *,
    store_id: str,
    history: list[ChatMessage] | None = None,
    max_steps: int = 5,
) -> AgentResult:
    """Run the tool-calling loop and return the final answer with a trace."""
    messages = _build_messages(question, store_id, history)
    trace: list[dict] = []

    for step in range(1, max_steps + 1):
        response = _call_llm(messages, TOOL_DEFINITIONS)
        message = response["choices"][0]["message"]
        messages.append(_assistant_turn(message))

        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            logger.info(
                "Agent finished",
                llm_calls=step,
                tool_calls=len(trace),
                store_id=store_id,
            )
            return _make_result(
                answer=message.get("content") or "",
                trace=trace,
                llm_calls=step,
            )

        for call in tool_calls:
            logger.info("Tool call", tool=call["function"]["name"], step=step)
            output = _dispatch(call)
            trace.append(_trace_entry(step, call, output))
            messages.append(_tool_turn(call, output))

    logger.warning("Agent hit max_steps", max_steps=max_steps, store_id=store_id)
    return _make_result(
        answer="Stopped: too many tool steps without a final answer.",
        trace=trace,
        llm_calls=max_steps,
    )


def answer_with_tools(
    question: str,
    store_id: str,
    history: list[ChatMessage] | None = None,
) -> str:
    """UI entry point — matches answer_question()'s signature in service/rag.py."""
    result = run_agent(question, store_id=store_id, history=history)
    return result["answer"]
