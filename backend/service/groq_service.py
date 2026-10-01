"""Groq implementation of the language-model service."""

from functools import cached_property

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from pydantic import SecretStr

from config import get_settings, require
from constants import GROQ_MODEL
from service.llm_service import LLMService


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
                require(self._api_key or get_settings().groq_api_key, "GROQ_API_KEY")
            ),
            temperature=0,
        )

    def generate(self, system_prompt: str, user_message: str) -> str:
        response = self._client.invoke(
            [SystemMessage(content=system_prompt), HumanMessage(content=user_message)]
        )
        return response.text
