"""OpenRouter chat completions, including the application's local tools."""

import json
from collections.abc import Sequence
from time import perf_counter
from typing import Any

import requests
import structlog
from pydantic import SecretStr

from config import get_settings, require
from constants import LLM_MAX_TOOL_ROUNDS, OPENROUTER_API_URL, OPENROUTER_MODEL
from domain.chat import ChatMessage
from domain.tools import Tool
from service.llm_service import LLMService

logger = structlog.stdlib.get_logger(__name__)


class OpenRouterService(LLMService):
    def __init__(
        self,
        model: str = OPENROUTER_MODEL,
        api_key: SecretStr | None = None,
    ) -> None:
        self.model = model
        self._api_key = api_key

    @staticmethod
    def _messages(
        system_prompt: str,
        user_message: str,
        history: Sequence[ChatMessage] | None,
    ) -> list[dict[str, Any]]:
        return [
            {"role": "system", "content": system_prompt},
            *[dict(message) for message in history or []],
            {"role": "user", "content": user_message},
        ]

    def _complete(
        self,
        messages: list[dict[str, Any]],
        tools: Sequence[Tool] = (),
    ) -> dict[str, Any]:
        api_key = require(
            self._api_key or get_settings().openrouter_api_key,
            "OPENROUTER_KEY",
        )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": 8192,
            "reasoning": {"enabled": False},
        }
        if tools:
            payload.update(
                tools=[tool.schema() for tool in tools],
                tool_choice="auto",
                provider={"require_parameters": True},
            )
        started = perf_counter()
        response = requests.post(
            OPENROUTER_API_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=(10, 120),
        )
        if not response.ok:
            # Provider messages may echo input; log only the HTTP status.
            raise requests.HTTPError(
                f"OpenRouter generation failed ({response.status_code})",
                response=response,
            )
        body = response.json()
        if not isinstance(body, dict) or body.get("error"):
            raise RuntimeError("OpenRouter returned an error response.")
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("OpenRouter returned no completion.")
        choice = choices[0]
        if not isinstance(choice, dict) or choice.get("finish_reason") in (
            "error",
            "length",
            "content_filter",
        ):
            raise RuntimeError("OpenRouter did not return a complete answer.")
        message = choice.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            raise RuntimeError("OpenRouter returned an invalid assistant message.")
        logger.info(
            "OpenRouter generation completed",
            model=self.model,
            messages=len(messages),
            tokens=body.get("usage"),
            duration_ms=round((perf_counter() - started) * 1000, 2),
        )
        return message

    @staticmethod
    def _answer(message: dict[str, Any]) -> str:
        content = message.get("content")
        if (
            message.get("tool_calls")
            or not isinstance(content, str)
            or not content.strip()
        ):
            raise RuntimeError(
                "The model returned an empty answer or unresolved tool calls.",
            )
        return content

    def generate(
        self,
        system_prompt: str,
        user_message: str,
        history: Sequence[ChatMessage] | None = None,
    ) -> str:
        return self._answer(
            self._complete(self._messages(system_prompt, user_message, history)),
        )

    def generate_with_tools(
        self,
        system_prompt: str,
        user_message: str,
        tools: Sequence[Tool],
        history: Sequence[ChatMessage] | None = None,
    ) -> str:
        if not tools:
            return self.generate(system_prompt, user_message, history)
        messages = self._messages(system_prompt, user_message, history)
        by_name = {tool.name: tool for tool in tools}
        for _ in range(LLM_MAX_TOOL_ROUNDS):
            message = self._complete(messages, tools)
            calls = message.get("tool_calls")
            if not calls:
                return self._answer(message)
            if not isinstance(calls, list) or not all(
                isinstance(call, dict) for call in calls
            ):
                raise RuntimeError("OpenRouter returned invalid tool calls.")
            # Keep the provider's message intact, including reasoning_details.
            messages.append(message)
            for call in calls:
                call_id = call.get("id") if isinstance(call, dict) else None
                function = call.get("function") if isinstance(call, dict) else None
                if not call_id or not isinstance(function, dict):
                    raise RuntimeError("OpenRouter returned an invalid tool call.")
                name = function.get("name")
                tool = by_name.get(name) if isinstance(name, str) else None
                try:
                    arguments = json.loads(function.get("arguments", ""))
                    if not isinstance(arguments, dict):
                        raise TypeError("Tool arguments must be an object")
                except (TypeError, ValueError):
                    result = "Invalid tool arguments. Supply a JSON object."
                else:
                    result = (
                        tool.run(arguments)
                        if tool
                        else f"There is no tool named {name}."
                    )
                logger.info("Tool called", tool=name, known=bool(tool))
                messages.append(
                    {"role": "tool", "tool_call_id": call_id, "content": result},
                )
        # Nemotron's free endpoint does not accept tool_choice="none". Finish
        # with the collected results and no offered tools, without reexecuting.
        messages.append(
            {
                "role": "user",
                "content": "Answer the original question using the tool results above. No more tools are available for this answer.",
            },
        )
        return self._answer(self._complete(messages))
