"""Groq implementation of the language-model service."""

from collections.abc import Sequence
from contextlib import suppress
from functools import cached_property
from time import perf_counter
from typing import Any

import groq
import httpx
import structlog
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_groq import ChatGroq
from pydantic import SecretStr

from config import get_settings, require
from constants import GROQ_MODEL, LLM_MAX_TOOL_ROUNDS
from domain.chat import ChatMessage
from domain.tools import Tool
from service.llm_service import LLMService
from service.provider_tracing import http_metadata, usage_fields
from service.tracing import observation

logger = structlog.stdlib.get_logger(__name__)

# Tool rounds before the model must answer in text.
MAX_TOOL_ROUNDS = LLM_MAX_TOOL_ROUNDS
# Requests per answer when Groq rejects a malformed tool call.
TOOL_ATTEMPTS = 2
TOOLS_UNAVAILABLE = (
    "(System note: your tools could not be used for this answer. Do not say you "
    "proposed, prepared or changed anything with a tool. If the manager asked to "
    "change a setting, say it could not be prepared this time and ask them to try "
    "again or use the Settings tab. For operational questions, say the required "
    "dispatch data could not be reached and do not invent figures.)"
)


class TracedGroqHTTPClient(groq.DefaultHttpxClient):
    """Observe each SDK transport attempt while retaining Groq's HTTP defaults."""

    def send(self, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        retry = request.headers.get("x-stainless-retry-count", "0")
        attempt = (
            int(retry) + 1
            if retry.isascii() and retry.isdigit() and len(retry) < 4
            else None
        )
        with observation(
            "groq.http_attempt",
            kind="span",
            metadata={
                "provider": "groq",
                "attempt": attempt,
                "http_method": request.method,
            },
        ) as span:
            response = super().send(request, **kwargs)
            span.update(metadata=http_metadata(response))
            if response.status_code >= 400:
                span.update(
                    level="WARNING",
                    status_message=f"HTTP_{response.status_code}",
                )
            return response


class GroqService(LLMService):
    def __init__(
        self,
        model: str = GROQ_MODEL,
        api_key: SecretStr | None = None,
    ) -> None:
        self.model = model
        self._api_key = api_key

    @cached_property
    def _client(self) -> ChatGroq:
        try:
            return ChatGroq(
                model=self.model,
                api_key=SecretStr(
                    require(
                        self._api_key or get_settings().groq_api_key,
                        "GROQ_API_KEY",
                    ),
                ),
                temperature=0,
                http_client=self._http_client,
            )
        except BaseException:
            with suppress(Exception):
                self.close()
            raise

    @cached_property
    def _http_client(self) -> TracedGroqHTTPClient:
        return TracedGroqHTTPClient()

    def close(self) -> None:
        """Close the transport owned by this service, including failed construction."""
        client = self.__dict__.get("_http_client")
        if client is not None:
            client.close()

    def __del__(self) -> None:
        # Unlike the SDK-created wrapper, a supplied httpx client has no finalizer.
        with suppress(Exception):
            self.close()

    @staticmethod
    def _messages(
        system_prompt: str,
        user_message: str,
        history: Sequence[ChatMessage] | None,
    ) -> list[BaseMessage]:
        messages: list[BaseMessage] = [SystemMessage(content=system_prompt)]
        for message in history or []:
            message_type = HumanMessage if message["role"] == "user" else AIMessage
            messages.append(message_type(content=message["content"]))
        messages.append(HumanMessage(content=user_message))
        return messages

    def generate(
        self,
        system_prompt: str,
        user_message: str,
        history: Sequence[ChatMessage] | None = None,
    ) -> str:
        messages = self._messages(system_prompt, user_message, history)
        started = perf_counter()
        response = self._invoke(self._client, messages)
        logger.info(
            "Groq generation completed",
            model=self.model,
            messages=len(messages),
            tokens=getattr(response, "usage_metadata", None),
            duration_ms=round((perf_counter() - started) * 1000, 2),
        )
        return self._answer_text(response, messages)

    def _invoke(
        self,
        client: Any,
        messages: list[BaseMessage],
        *,
        schemas: list[dict] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AIMessage:
        """Trace each provider request, including tool rounds and empty retries."""
        with observation(
            "groq.completion",
            kind="generation",
            input={
                "messages": [
                    {
                        "role": message.type,
                        "content": message.content,
                        **(
                            {"tool_calls": message.tool_calls}
                            if isinstance(message, AIMessage) and message.tool_calls
                            else {}
                        ),
                        **(
                            {"tool_call_id": message.tool_call_id}
                            if isinstance(message, ToolMessage)
                            else {}
                        ),
                    }
                    for message in messages
                ],
                "tools": schemas or [],
            },
            model=self.model,
            metadata={
                "provider": "groq",
                "upstream_provider": "groq",
                "usage_source": "unknown",
                "cost_source": "unknown",
                **(metadata or {}),
            },
        ) as span:
            span.update(model_parameters={"temperature": 0})
            try:
                response = client.invoke(messages)
            except groq.APIError as exc:
                span.update(metadata=http_metadata(getattr(exc, "response", None)))
                raise
            details = getattr(response, "response_metadata", {}) or {}
            usage = getattr(response, "usage_metadata", {}) or {}
            raw_usage = details.get("token_usage", {})
            span.update(
                **usage_fields(
                    {**(raw_usage if isinstance(raw_usage, dict) else {}), **usage},
                    provider="groq",
                ),
            )
            span.update(
                output={
                    "content": getattr(response, "content", response.text),
                    "tool_calls": getattr(response, "tool_calls", []),
                    "invalid_tool_calls": getattr(response, "invalid_tool_calls", []),
                    "reasoning": {
                        name: value
                        for name, value in getattr(
                            response,
                            "additional_kwargs",
                            {},
                        ).items()
                        if name
                        in {"reasoning", "reasoning_content", "reasoning_details"}
                    },
                },
                model=details.get("model_name") or self.model,
                metadata={
                    "response_id": getattr(response, "id", None),
                    "finish_reason": details.get("finish_reason"),
                },
            )
            for call in getattr(response, "invalid_tool_calls", []):
                with observation(
                    "tool.invalid_arguments",
                    kind="tool",
                    input=call,
                ) as rejected:
                    rejected.update(
                        output={"error": "INVALID_TOOL_ARGUMENTS"},
                        level="WARNING",
                        status_message="INVALID_TOOL_ARGUMENTS",
                    )
            if not response.text.strip() and not getattr(response, "tool_calls", []):
                span.update(level="WARNING", status_message="EMPTY_ANSWER")
            return response

    def _answer_text(
        self,
        response: AIMessage,
        messages: list[BaseMessage],
        schemas: list[dict] | None = None,
    ) -> str:
        """The reply's text; one retry with low reasoning effort if it is empty.

        gpt-oss models can spend the whole output budget on reasoning and
        return no text. Retrying the same request with less reasoning usually
        leaves room for the answer; if it is still empty, fail rather than save
        an empty reply.
        """
        if response.text.strip():
            return response.text
        logger.warning(
            "Groq returned an empty answer; retrying with low reasoning effort",
            model=self.model,
            tokens=getattr(response, "usage_metadata", None),
        )
        client: Any = self._client.model_copy(update={"reasoning_effort": "low"})
        if schemas:
            client = client.bind_tools(schemas, tool_choice="none")
        retry = self._invoke(
            client,
            messages,
            schemas=schemas,
            metadata={"retry": "empty_answer", "reasoning_effort": "low"},
        )
        if not retry.text.strip():
            logger.error("Model returned no answer")
            raise RuntimeError("The model returned an empty answer.")
        return retry.text

    def generate_with_tools(
        self,
        system_prompt: str,
        user_message: str,
        tools: Sequence[Tool],
        history: Sequence[ChatMessage] | None = None,
    ) -> str:
        """Run the model's tool calls, then return its final text answer.

        Groq rejects a malformed tool call with a 400. That is retried once; if it
        fails again, the model answers without tools and is told so, so it cannot
        claim it used one.
        """
        if not tools:
            return self.generate(system_prompt, user_message, history)
        started = perf_counter()
        for attempt in range(1, TOOL_ATTEMPTS + 1):
            try:
                response, messages, calls = self._run_tools(
                    self._messages(system_prompt, user_message, history),
                    tools,
                )
            except groq.BadRequestError as exc:
                logger.warning(
                    "Groq rejected a tool call",
                    attempt=attempt,
                    error=getattr(exc, "body", None) or str(exc),
                )
                continue
            logger.info(
                "Groq generation completed",
                model=self.model,
                messages=len(messages),
                tool_calls=calls,
                tokens=getattr(response, "usage_metadata", None),
                duration_ms=round((perf_counter() - started) * 1000, 2),
            )
            return self._answer_text(
                response,
                messages,
                [tool.schema() for tool in tools],
            )
        logger.warning("Answering without tools after rejected tool calls")
        return self.generate(
            system_prompt,
            f"{user_message}\n\n{TOOLS_UNAVAILABLE}",
            history,
        )

    def _run_tools(
        self,
        messages: list[BaseMessage],
        tools: Sequence[Tool],
    ) -> tuple[AIMessage, list[BaseMessage], int]:
        by_name = {tool.name: tool for tool in tools}
        schemas = [tool.schema() for tool in tools]
        calls = 0
        for round_number in range(MAX_TOOL_ROUNDS + 1):
            # The last round forbids tools, so the model has to answer.
            client = self._client.bind_tools(
                schemas,
                tool_choice="none" if round_number == MAX_TOOL_ROUNDS else "auto",
            )
            response = self._invoke(
                client,
                messages,
                schemas=schemas,
                metadata={
                    "tool_round": round_number,
                    "tool_choice": (
                        "none" if round_number == MAX_TOOL_ROUNDS else "auto"
                    ),
                },
            )
            if not response.tool_calls:
                break
            messages.append(response)
            for call in response.tool_calls:
                tool = by_name.get(call["name"])
                if tool:
                    result = tool.run(call["args"])
                else:
                    result = f"There is no tool named {call['name']}."
                    with observation("tool.unknown", kind="tool", input=call) as span:
                        span.update(
                            output=result,
                            level="WARNING",
                            status_message="UNKNOWN_TOOL",
                        )
                calls += 1
                logger.info("Tool called", tool=call["name"], known=bool(tool))
                messages.append(
                    ToolMessage(content=result, tool_call_id=call["id"] or ""),
                )
        return response, messages, calls
