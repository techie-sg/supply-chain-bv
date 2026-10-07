"""Tool-calling agent loop for DispatchDesk.

Tools are served by the dispatchdesk-ops MCP server (mcp_server/server.py),
started over stdio once per request and shared by every tool call in the loop.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from collections.abc import Sequence
from concurrent.futures import Future
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, Self
from zoneinfo import ZoneInfo

import requests
import structlog
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

from config import get_settings, require
from constants import GROQ_MODEL
from domain.chat import ChatMessage

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
# MCP client
# ---------------------------------------------------------------------------

BACKEND_DIR = Path(__file__).resolve().parents[1]
STARTUP_TIMEOUT_SEC = 30  # starting the server imports the app's modules
TOOL_TIMEOUT_SEC = 5  # per call, as set in docs/tools.md
RATE_LIMIT_ATTEMPTS = 4  # Groq's free tier allows 8,000 tokens a minute
MAX_WAIT_SEC = 20


def _error_text(code: str, message: str, **details: Any) -> str:
    return json.dumps(
        {"error": {"code": code, "message": message, "details": details}},
    )


class ToolSession(Protocol):
    """What the agent loop needs from the tool server; tests supply a fake."""

    def tool_definitions(self) -> list[dict]: ...

    def call(self, name: str, arguments: dict[str, Any]) -> str: ...


class ToolClient:
    """Sync handle on the MCP server: one subprocess for the whole agent run.

    The MCP client is async, so it lives on its own event loop in a thread. One
    task opens it, waits to be told to stop, then closes it, because the stdio
    transport must be entered and exited from the same task.
    """

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._ready = threading.Event()
        self._stop: asyncio.Event | None = None
        self._serving: Future[None] | None = None
        self._client: Client | None = None

    def _run(self, coroutine: Any, timeout: float) -> Any:
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        try:
            return future.result(timeout)
        except BaseException:
            future.cancel()
            raise

    async def _serve(self) -> None:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "mcp_server.server"],
            cwd=str(BACKEND_DIR),
            env=dict(os.environ),  # the server needs DATABASE_URL
        )
        self._stop = asyncio.Event()
        try:
            async with Client(params) as client:
                self._client = client
                self._ready.set()
                await self._stop.wait()
        finally:
            self._ready.set()  # unblock __enter__ if startup failed

    def __enter__(self) -> Self:
        self._thread.start()
        self._serving = asyncio.run_coroutine_threadsafe(self._serve(), self._loop)
        if not self._ready.wait(STARTUP_TIMEOUT_SEC) or self._client is None:
            error = (
                self._serving.exception()
                if self._serving.done() and not self._serving.cancelled()
                else None
            )
            self.close()
            logger.error("Could not start the MCP server", error=repr(error))
            raise RuntimeError("Dispatch tools are unavailable.") from error
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._serving is not None and self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
            try:
                self._serving.result(TOOL_TIMEOUT_SEC)
            except Exception:
                logger.warning("MCP server did not shut down cleanly", exc_info=True)
        elif self._serving is not None:
            self._serving.cancel()
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=TOOL_TIMEOUT_SEC)

    def tool_definitions(self) -> list[dict]:
        """The server's tools in the function format the model expects."""
        assert self._client is not None
        tools = self._run(self._client.list_tools(), TOOL_TIMEOUT_SEC).tools
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            }
            for tool in tools
        ]

    def call(self, name: str, arguments: dict[str, Any]) -> str:
        """Run one tool; always returns a JSON string, errors included."""
        assert self._client is not None
        try:
            result = self._run(
                self._client.call_tool(
                    name,
                    arguments,
                    read_timeout_seconds=TOOL_TIMEOUT_SEC,
                ),
                TOOL_TIMEOUT_SEC + 1,
            )
        except TimeoutError:
            return _error_text(
                "DATA_UNAVAILABLE",
                "The dispatch tool timed out. Retry once; if it fails again, tell "
                "the manager live data cannot be reached.",
                retryable=True,
            )
        text = "".join(block.text for block in result.content if block.type == "text")
        if result.is_error:
            try:
                json.loads(text)
            except json.JSONDecodeError:  # rejected by the MCP layer, not the tool
                return _error_text("INVALID_INPUT", text)
        return text


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
    """One raw Groq chat/completions call. Returns the full response dict.

    A tokens-per-minute 429 is retried after the wait Groq asks for.
    """
    api_key = require(get_settings().groq_api_key, "GROQ_API_KEY")
    for attempt in range(1, RATE_LIMIT_ATTEMPTS + 1):
        response = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": MODEL,
                "messages": messages,
                "tools": tools,
                "temperature": 0,
            },
            timeout=60,
        )
        if response.status_code != 429 or attempt == RATE_LIMIT_ATTEMPTS:
            break
        wait = min(float(response.headers.get("retry-after", 1)) + 0.5, MAX_WAIT_SEC)
        logger.warning("Groq rate limit; retrying", wait_sec=wait, attempt=attempt)
        time.sleep(wait)
    response.raise_for_status()
    return response.json()


def _dispatch(call: dict, client: ToolSession) -> str:
    """Run one tool call through the MCP server and return its JSON result."""
    try:
        arguments = json.loads(call["function"]["arguments"] or "{}")
    except json.JSONDecodeError:
        return _error_text("INVALID_INPUT", "Arguments were not valid JSON.")
    return client.call(call["function"]["name"], arguments)


def _build_messages(
    question: str,
    store_id: str,
    history: Sequence[ChatMessage] | None,
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
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        parsed = None
    return {
        "step": step,
        "tool": call["function"]["name"],
        "arguments": call["function"]["arguments"],
        "error": parsed.get("error", {}).get("code")
        if isinstance(parsed, dict)
        else None,
        "as_of": parsed.get("as_of") if isinstance(parsed, dict) else None,
        "stale": parsed.get("stale") if isinstance(parsed, dict) else None,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_agent(
    question: str,
    *,
    store_id: str,
    history: Sequence[ChatMessage] | None = None,
    max_steps: int = 5,
    client: ToolSession | None = None,
    user_message: str | None = None,
) -> AgentResult:
    """Run the tool-calling loop and return the final answer with a trace.

    `user_message` replaces the bare question, so the caller can include
    retrieved guidance, the manager's settings and a conversation summary.
    """
    with nullcontext(client) if client else ToolClient() as tools:
        definitions = tools.tool_definitions()
        messages = _build_messages(user_message or question, store_id, history)
        trace: list[dict] = []

        for step in range(1, max_steps + 1):
            response = _call_llm(messages, definitions)
            message = response["choices"][0]["message"]
            messages.append(_assistant_turn(message))

            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                if not (message.get("content") or "").strip():
                    logger.error(
                        "Model returned no answer",
                        finish_reason=response["choices"][0].get("finish_reason"),
                    )
                    raise RuntimeError("The model returned an empty answer.")
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
                output = _dispatch(call, tools)
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
