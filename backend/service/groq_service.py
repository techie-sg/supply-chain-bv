"""Groq implementation of the language-model service."""

from collections.abc import Sequence
from functools import cached_property
from time import perf_counter

import groq
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
from constants import GROQ_MODEL
from domain.chat import ChatMessage
from domain.tools import Tool
from service.llm_service import LLMService

logger = structlog.stdlib.get_logger(__name__)

# Tool rounds before the model must answer in text.
MAX_TOOL_ROUNDS = 3
# Requests per answer when Groq rejects a malformed tool call.
TOOL_ATTEMPTS = 2
TOOLS_UNAVAILABLE = (
    "(System note: your tools could not be used for this answer. Do not say you "
    "proposed, prepared or changed anything with a tool. If the manager asked to "
    "change a setting, say it could not be prepared this time and ask them to try "
    "again or use the Settings tab. For operational questions, say the required "
    "dispatch data could not be reached and do not invent figures.)"
)


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
        return ChatGroq(
            model=self.model,
            api_key=SecretStr(
                require(self._api_key or get_settings().groq_api_key, "GROQ_API_KEY"),
            ),
            temperature=0,
        )

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
        response = self._client.invoke(messages)
        logger.info(
            "Groq generation completed",
            model=self.model,
            messages=len(messages),
            tokens=getattr(response, "usage_metadata", None),
            duration_ms=round((perf_counter() - started) * 1000, 2),
        )
        return self._answer_text(response)

    @staticmethod
    def _answer_text(response: AIMessage) -> str:
        if not response.text.strip():
            logger.error("Model returned no answer")
            raise RuntimeError("The model returned an empty answer.")
        return response.text

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
            return self._answer_text(response)
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
            response = client.invoke(messages)
            if not response.tool_calls:
                break
            messages.append(response)
            for call in response.tool_calls:
                tool = by_name.get(call["name"])
                result = (
                    tool.run(call["args"])
                    if tool
                    else f"There is no tool named {call['name']}."
                )
                calls += 1
                logger.info("Tool called", tool=call["name"], known=bool(tool))
                messages.append(
                    ToolMessage(content=result, tool_call_id=call["id"] or ""),
                )
        return response, messages, calls
