"""Dreaming learns durable personal context from new manager messages.

Chat only reads this context. Summary cron reviews bounded raw message batches;
ordinary operational messages normally produce no change.
"""

import json
from collections.abc import Callable
from datetime import datetime
from html import escape
from typing import Any

import structlog
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import Engine

from constants import (
    DEMO_MANAGER_ID,
    DEMO_STORE_ID,
    PERSONALIZATION_BATCH_BYTES,
    PERSONALIZATION_BATCH_MESSAGES,
    PERSONALIZATION_BATCHES_PER_CHAT,
    PERSONALIZATION_MAX_INPUT_BYTES,
    TIMEZONE,
)
from database.models import Conversation
from domain.memory import SuggestionKind
from domain.personalization import (
    NOTES,
    PersonalizationCandidate,
    Profile,
    describe,
    validate_value,
)
from queries.personalization import (
    finish_review,
    read_profile,
    save_profile,
)
from resources import PROMPTS

logger = structlog.stdlib.get_logger(__name__)


def item(
    value: str | None,
    source: str,
    *,
    conversation_id: str | None = None,
    quote: str = "",
) -> dict[str, Any]:
    return {
        "value": value,
        "source": source,
        "conversation_id": conversation_id,
        "quote": quote,
        "saved_at": datetime.now(TIMEZONE).isoformat(),
    }


class PersonalizationService:
    def __init__(self, store_id: str, manager_id: str, engine: Engine | None = None):
        self.store_id, self.manager_id, self.engine = store_id, manager_id, engine

    def profile(self) -> Profile:
        return read_profile(self.store_id, self.manager_id, self.engine)

    def save(
        self,
        values: dict[str, str | None],
        expected: Profile | None = None,
    ) -> bool:
        validated = {
            code: validate_value(code, value) for code, value in values.items()
        }
        current = expected if expected is not None else self.profile()
        changes = {
            code: item(value, "settings")
            for code, value in validated.items()
            if current.get(code, {}).get("value") != value
        }
        if not changes:
            return False
        return save_profile(
            self.store_id,
            self.manager_id,
            changes,
            current,
            self.engine,
        )

    def prompt_block(self) -> str | None:
        profile = self.profile()
        lines = [
            describe(code, validate_value(code, entry.get("value")))
            for code, entry in profile.items()
            if entry.get("value")
        ]
        if not lines:
            return None
        return escape("\n".join(lines), quote=False)

    def review(
        self,
        conversation: Conversation,
        generate: Callable[[str, str], str],
    ) -> int:
        """Save supported personal context from bounded batches after this chat's marker."""
        if (conversation.store_id, conversation.manager_id) != (
            self.store_id,
            self.manager_id,
        ):
            raise ValueError("Personalization review belongs to another manager.")
        if conversation.summary is None or conversation.summary_covers_to is None:
            return 0
        saved = 0
        for _ in range(PERSONALIZATION_BATCHES_PER_CHAT):
            expected = conversation.personalization_covers_to
            start = 0 if expected is None else expected + 1
            if start > conversation.summary_covers_to:
                break
            end = self._batch_end(conversation, start)
            updates = self._updates(
                conversation,
                start,
                end,
                generate,
            )
            count = finish_review(
                conversation.id,
                self.store_id,
                self.manager_id,
                expected,
                end,
                updates,
                self.engine,
            )
            if count is None:
                break  # another worker completed or moved this batch
            conversation.personalization_covers_to = end
            saved += count
            logger.info(
                "Personalization batch reviewed",
                conversation_id=str(conversation.id),
                covers_to=end,
                preferences_saved=count,
            )
        return saved

    @staticmethod
    def _batch_end(conversation: Conversation, start: int) -> int:
        assert conversation.summary_covers_to is not None
        end = min(
            conversation.summary_covers_to,
            start + PERSONALIZATION_BATCH_MESSAGES - 1,
        )
        size = 0
        for index in range(start, end + 1):
            message = conversation.messages[index]
            if message["who"] != "manager":
                continue
            size += len(json.dumps(message["what"]).encode("utf-8")) + 128
            if size > PERSONALIZATION_BATCH_BYTES:
                if index == start:
                    raise ValueError(
                        "A personalization request exceeds the batch input limit.",
                    )
                return index - 1
        return end

    def _updates(
        self,
        conversation: Conversation,
        start: int,
        end: int,
        generate: Callable[[str, str], str],
    ) -> list[dict[str, Any]]:
        """Extract personal context from raw manager messages, outside transactions."""
        fresh = [
            (index, message)
            for index, message in enumerate(
                conversation.messages[start : end + 1],
                start,
            )
            if message["who"] == "manager" and message["what"].strip()
        ]
        if not fresh:
            return []
        profile = self.profile()
        current = self.prompt_block() or ""
        adapter = TypeAdapter(list[PersonalizationCandidate])
        prompt = (PROMPTS / "personalization.md").read_text(encoding="utf-8")
        prompt += "\nRequired JSON schema:\n" + json.dumps(adapter.json_schema())
        payload = json.dumps(
            {
                "current_personal_context": current,
                "manager_messages": [
                    {
                        "conversation_id": str(conversation.id),
                        "message_index": index,
                        "text": message["what"],
                    }
                    for index, message in fresh
                ],
            },
        )
        if (
            len(prompt.encode()) + len(payload.encode())
            > PERSONALIZATION_MAX_INPUT_BYTES
        ):
            raise ValueError("Personalization extraction input exceeds its budget.")
        try:
            candidates = adapter.validate_json(generate(prompt, payload))
        except (ValidationError, ValueError) as exc:
            raise ValueError(
                "Invalid personalization extraction; batch remains pending.",
            ) from exc
        if len(candidates) > 1:
            raise ValueError("Return one merged personal context or no update.")
        texts = {index: message["what"] for index, message in fresh}
        updates = []
        for candidate in candidates:
            if candidate.code != NOTES:
                continue
            value = validate_value(NOTES, candidate.value)
            if value == profile.get(NOTES, {}).get("value"):
                continue
            if not all(
                entry.conversation_id == str(conversation.id)
                and entry.message_index in texts
                and entry.quote in texts[entry.message_index]
                for entry in candidate.evidence
            ):
                continue
            updates.append(
                {
                    "store_id": self.store_id,
                    "manager_id": self.manager_id,
                    "kind": SuggestionKind.PERSONALIZATION,
                    "payload": {"code": NOTES, "value": candidate.value},
                    "reason": candidate.reason,
                    "evidence": [entry.model_dump() for entry in candidate.evidence],
                    "expected_profile": profile,
                },
            )
        return updates


def manager_personalization(
    manager_id: str = DEMO_MANAGER_ID,
) -> PersonalizationService:
    return PersonalizationService(DEMO_STORE_ID, manager_id)
