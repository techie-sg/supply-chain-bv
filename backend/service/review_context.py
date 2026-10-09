"""Chat evidence formatting shared by review, handover and memory generation."""

from datetime import date, datetime

from constants import TIMEZONE
from database.models import Conversation
from domain.chat import StoredMessage


def chat_label(conversation: Conversation) -> str:
    first = conversation.messages[0]["what"] if conversation.messages else ""
    return conversation.title or " ".join(first.split())[:60]


def message_day(message: StoredMessage) -> date:
    return datetime.fromisoformat(message["when"]).astimezone(TIMEZONE).date()


def short_date(day: date) -> str:
    return f"{day.day} {day.strftime('%b')}"


def chat_evidence(
    conversation: Conversation,
    *positions: int,
) -> list[dict[str, object]]:
    if not positions:
        return [{"conversation_id": str(conversation.id)}]
    return [
        {"conversation_id": str(conversation.id), "position": position}
        for position in positions
    ]
