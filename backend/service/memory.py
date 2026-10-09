"""Build the manager memory digest and supply its context to chat/UI."""

from collections.abc import Callable
from datetime import datetime, timedelta

import structlog
from sqlalchemy import Engine

from constants import (
    DEMO_MANAGER_ID,
    DIGEST_DAYS,
    DIGEST_MAX_CHATS,
    DIGEST_MAX_ITEMS,
    TIMEZONE,
)
from domain.suggestions import MemoryView
from queries.dreaming import get_digest, recent_conversations, save_digest
from resources import PROMPTS
from service.review_context import chat_label, message_day, short_date

logger = structlog.stdlib.get_logger(__name__)


def bullets(text: str) -> str | None:
    """The reply's bullet lines, at most DIGEST_MAX_ITEMS; None if there are none."""
    items = [
        "- " + line.strip()[1:].strip()
        for line in text.splitlines()
        if line.strip()[:1] in {"-", "*", "•"} and line.strip()[1:].strip()
    ]
    return "\n".join(items[:DIGEST_MAX_ITEMS]) or None


class MemoryService:
    def __init__(
        self,
        generate: Callable[[str, str], str],
        engine: Engine | None = None,
    ):
        self.generate = generate
        self.engine = engine

    def _prompt(self, name: str) -> str:
        return (PROMPTS / name).read_text(encoding="utf-8")

    def memory_digest(self, store_id: str, manager_id: str, now: datetime) -> bool:
        """Rebuild the manager's digest from the last week's chat summaries.

        Returns whether the stored digest changed. When the chats and how far
        their summaries reach equal the stored sources, the model isn't called.
        """
        since = now - timedelta(days=DIGEST_DAYS)
        chats = [
            conversation
            for conversation in recent_conversations(
                store_id,
                manager_id,
                50,
                self.engine,
            )
            if conversation.summary
            and datetime.fromisoformat(conversation.messages[-1]["when"]) >= since
        ][:DIGEST_MAX_CHATS]
        sources = [
            {
                "conversation_id": str(conversation.id),
                "covers_to": conversation.summary_covers_to,
            }
            for conversation in chats
        ]
        current = get_digest(manager_id, self.engine)
        if (current.sources if current else []) == sources:
            return False
        digest = None
        if chats:
            reply = self.generate(
                self._prompt("memory_digest.md"),
                "\n\n".join(
                    f"Chat on {short_date(message_day(conversation.messages[-1]))}: "
                    f"{chat_label(conversation)}\n{conversation.summary}"
                    for conversation in reversed(chats)
                ),
            )
            digest = bullets(reply)
        save_digest(store_id, manager_id, digest, sources, self.engine)
        logger.info("Memory digest rebuilt", manager_id=manager_id, chats=len(chats))
        return True


def memory_digest(
    manager_id: str = DEMO_MANAGER_ID,
    engine: Engine | None = None,
) -> MemoryView | None:
    row = get_digest(manager_id, engine)
    return MemoryView(row.digest, row.sources, row.built_at) if row else None


def memory_block(
    manager_id: str = DEMO_MANAGER_ID,
    engine: Engine | None = None,
) -> str | None:
    """The manager's digest for the model, if there is one."""
    digest = get_digest(manager_id, engine)
    if digest is None or not digest.digest:
        return None
    built = digest.built_at.astimezone(TIMEZONE).date()
    return (
        f"From this manager's chats in the {DIGEST_DAYS} days before "
        f"{short_date(built)}:\n{digest.digest}"
    )
