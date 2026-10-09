"""Change durable preferences only on explicit requests or approved suggestions.

Summary review sees only new manager messages. Ordinary dispatch questions do
not read evidence, call an extraction model, or write personalization. Repeated
style requests need three distinct chats; extraction only creates proposals.
"""

import json
import re
from collections.abc import Callable
from datetime import datetime
from html import escape
from typing import Any
from uuid import UUID

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
    PERSONALIZATION_MIN_CHATS,
    TIMEZONE,
)
from database.models import Conversation
from domain.memory import SuggestionKind
from domain.personalization import (
    FIELDS,
    PersonalizationCandidate,
    Profile,
    describe,
    validate_value,
)
from domain.tools import Tool
from queries.personalization import (
    evidence_chats,
    finish_review,
    read_profile,
    save_profile,
)
from resources import PROMPTS

logger = structlog.stdlib.get_logger(__name__)

# Conservative English request forms. Questions, quoted instructions, assistant
# text, temporary shift conditions and operational settings are not candidates.
REQUEST = re.compile(
    r"^(?:please\s+)?(?:i (?:prefer|like|want)|always\b|from now on\b|"
    r"in (?:the )?future\b|remember (?:that |my )|keep (?:your |the )?"
    r"(?:answers?|responses?|replies?)\b|(?:give|show|start|use|make)\b|"
    r"(?:forget|remove|reset) (?:my |the )?(?:saved |preference|personalization))",
    re.IGNORECASE,
)
DURABLE = re.compile(
    r"\b(?:always|from now on|in (?:the )?future|remember|i prefer|by default|every (?:answer|response|reply)|forget|remove|reset)\b",
    re.IGNORECASE,
)
GENERAL_RESPONSE = re.compile(
    r"^(?:please\s+)?keep (?:your |the )?(?:answers|responses|replies)\s+"
    r"(?:short(?:er)?|brief|concise|balanced|detailed|thorough|longer|in.depth)\b",
    re.IGNORECASE,
)
TEMPORARY = re.compile(
    r"\b(?:(?:this|next|current) "
    r"(?:answer|response|reply|question|chat|conversation|time|day|shift)|"
    r"for now|today|tonight|tomorrow|until|just this|only this)\b",
    re.IGNORECASE,
)
NEGATED = re.compile(r"\b(?:don'?t|do not|never|not)\b", re.IGNORECASE)
SIGNALS = {
    "answer_length": {
        "brief": r"\b(?:short|shorter|brief|concise)\b",
        "balanced": r"\bbalanced\b",
        "detailed": r"\b(?:detailed|thorough|in.depth|longer)\b",
    },
    "answer_order": {
        "recommendation_first": r"(?:recommendation|action|next step).{0,25}\bfirst\b|\bstart\b.{0,30}(?:recommendation|action|next step)",
        "explanation_first": r"explanation.{0,25}\bfirst\b|\bstart\b.{0,30}explanation",
    },
    "comparisons": {
        "best_option": r"\b(?:one|single|best) (?:option|recommendation)\b",
        "alternatives": r"\b(?:alternatives|trade.offs|compare (?:the )?options)\b",
    },
    "decision_priority": {
        "existing_capacity": r"\b(?:existing|current) (?:team|capacity|riders)\b",
        "lower_cost": r"\b(?:lower cost|cheaper|lowest cost)\b",
        "service_quality": r"\bservice quality\b",
    },
}


def request_clauses(text: str) -> list[str]:
    return [
        part.strip()
        for part in re.split(r"[.!?\n]+", text)
        if REQUEST.search(part.strip())
        and not TEMPORARY.search(part)
        and not re.search(r'["“”`]', part)
    ]


def supports(code: str, value: str, quote: str) -> bool:
    pattern = SIGNALS.get(code, {}).get(value)
    if code == "answer_length" and not re.search(
        r"\b(?:answers?|responses?|replies|reply|explanations?)\b",
        quote,
        re.IGNORECASE,
    ):
        return False
    return bool(
        pattern
        and re.search(pattern, quote, re.IGNORECASE)
        and not NEGATED.search(quote),
    )


def eligible(text: str) -> bool:
    return any(
        supports(code, value, clause)
        for clause in request_clauses(text)
        for code, choices in SIGNALS.items()
        for value in choices
    )


def explicit(text: str) -> bool:
    return any(
        DURABLE.search(clause)
        or (
            GENERAL_RESPONSE.search(clause)
            and any(
                supports("answer_length", value, clause)
                for value in SIGNALS["answer_length"]
            )
        )
        for clause in request_clauses(text)
    )


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
        """Review bounded batches after this chat's own marker, never apply a profile."""
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
            proposals = self._proposals(
                conversation,
                start,
                end,
                conversation.summary,
                generate,
            )
            count = finish_review(
                conversation.id,
                self.store_id,
                self.manager_id,
                expected,
                end,
                proposals,
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
                proposals=count,
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
            if message["who"] != "manager" or not eligible(message["what"]):
                continue
            size += len(json.dumps(message["what"]).encode("utf-8")) + 128
            if size > PERSONALIZATION_BATCH_BYTES:
                if index == start:
                    raise ValueError(
                        "A personalization request exceeds the batch input limit.",
                    )
                return index - 1
        return end

    def _proposals(
        self,
        conversation: Conversation,
        start: int,
        end: int,
        summary: str,
        generate: Callable[[str, str], str],
    ) -> list[dict[str, Any]]:
        """Extract proposals outside any write transaction, from exact user evidence."""
        batch = conversation.messages[start : end + 1]
        fresh = [
            (index, message)
            for index, message in enumerate(batch, start)
            if message["who"] == "manager" and eligible(message["what"])
        ]
        if not fresh:
            return []
        profile = self.profile()
        allowed = {
            code: field.choices for code, field in FIELDS.items() if code not in profile
        }
        if not allowed:
            return []
        fresh = [
            (index, message)
            for index, message in fresh
            if any(
                supports(code, value, clause)
                for code, choices in allowed.items()
                for value in choices
                for clause in request_clauses(message["what"])
            )
        ]
        if not fresh:
            return []
        prompt = (PROMPTS / "personalization.md").read_text(encoding="utf-8")
        fresh_keys = {(str(conversation.id), index) for index, _ in fresh}
        messages: dict[tuple[str, int], str] = {
            (str(conversation.id), index): message["what"] for index, message in fresh
        }
        # This context is optional. Full user messages, never truncated quotes,
        # are retained as evidence; historical evidence fits only within the cap.
        summary = summary.encode("utf-8")[:2048].decode("utf-8", errors="ignore")

        def payload() -> str:
            return json.dumps(
                {
                    "allowed_preferences": allowed,
                    "new_messages": [
                        {"conversation_id": chat, "message_index": index}
                        for chat, index in sorted(fresh_keys)
                    ],
                    "manager_messages": [
                        {"conversation_id": chat, "message_index": index, "text": text}
                        for (chat, index), text in messages.items()
                    ],
                    "summary_for_context_only": summary,
                },
            )

        def fits() -> bool:
            return (
                len(prompt.encode("utf-8")) + len(payload().encode("utf-8"))
                <= PERSONALIZATION_MAX_INPUT_BYTES
            )

        if not fits():
            raise ValueError("Personalization extraction input exceeds its budget.")
        chats = evidence_chats(self.store_id, self.manager_id, self.engine)
        for chat in chats:
            for offset, message in enumerate(chat["messages"]):
                if message["who"] == "manager" and eligible(message["what"]):
                    key = (str(chat["id"]), chat["start"] + offset)
                    if key in messages or (
                        key[0] == str(conversation.id) and key[1] > end
                    ):
                        continue
                    messages[key] = message["what"]
                    if not fits():
                        del messages[key]
        # A single explicit request is enough for a proposal. Otherwise, require
        # three chats with matching style requests before paying for extraction.
        if not any(explicit(message["what"]) for _, message in fresh) and not any(
            len(
                {
                    chat
                    for (chat, _), text in messages.items()
                    if any(
                        supports(code, value, clause)
                        for clause in request_clauses(text)
                    )
                },
            )
            >= PERSONALIZATION_MIN_CHATS
            for code, choices in allowed.items()
            for value in choices
        ):
            return []
        raw = generate(prompt, payload())
        try:
            candidates = TypeAdapter(list[PersonalizationCandidate]).validate_json(raw)
        except (ValidationError, ValueError) as exc:
            raise ValueError(
                "Invalid personalization extraction; batch remains pending.",
            ) from exc
        proposals = []
        for candidate in candidates[: len(FIELDS)]:
            if (
                candidate.code not in allowed
                or candidate.value not in allowed[candidate.code]
            ):
                continue
            evidence = candidate.evidence
            keys = {(entry.conversation_id, entry.message_index) for entry in evidence}
            if len(keys) != len(evidence) or not keys.intersection(fresh_keys):
                continue
            if not all(
                entry.quote
                in messages.get((entry.conversation_id, entry.message_index), "")
                and any(
                    entry.quote.strip().rstrip(".!?") in clause
                    and supports(candidate.code, candidate.value, clause)
                    for clause in request_clauses(
                        messages.get((entry.conversation_id, entry.message_index), ""),
                    )
                )
                for entry in evidence
            ):
                continue
            if (
                not any(
                    explicit(entry.quote)
                    for entry in evidence
                    if (entry.conversation_id, entry.message_index) in fresh_keys
                )
                and len({entry.conversation_id for entry in evidence})
                < PERSONALIZATION_MIN_CHATS
            ):
                continue
            proposals.append(
                {
                    "store_id": self.store_id,
                    "manager_id": self.manager_id,
                    "kind": SuggestionKind.PERSONALIZATION,
                    "payload": {"code": candidate.code, "value": candidate.value},
                    "reason": candidate.reason,
                    "evidence": [entry.model_dump() for entry in evidence],
                },
            )
        return proposals


class PersonalizationChanges:
    """A turn-scoped tool; only a quoted explicit request authorizes a write."""

    def __init__(
        self,
        service: PersonalizationService,
        question: str,
        conversation_id: Callable[[], UUID | None],
    ):
        self.service, self.question, self.conversation_id = (
            service,
            question,
            conversation_id,
        )

    def tool(self) -> Tool:
        return Tool(
            "change_personalization",
            "Save or remove a lasting response preference ONLY when the latest manager message explicitly asks for it (always, from now on, I prefer, remember, forget/reset my preference), including general instructions such as 'keep answers short for me' with no temporary scope. Call this tool before acknowledging a saved preference. One-off requests apply to this answer only. Quote the exact request. This never changes operational settings or policy. Report saved only if this tool returns saved=true.",
            {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "enum": list(FIELDS)},
                    "value": {
                        "type": ["string", "null"],
                        "description": f"Allowed values: {json.dumps({code: field.choices for code, field in FIELDS.items()})}. Null removes the saved preference.",
                    },
                    "quote": {
                        "type": "string",
                        "description": "Exact quote from the latest manager message, including a general or explicitly lasting preference request.",
                    },
                },
                "required": ["code", "value", "quote"],
                "additionalProperties": False,
            },
            self.run,
        )

    def run(self, args: dict[str, Any]) -> str:
        try:
            code, value, quote = args["code"], args["value"], args["quote"]
            if (
                code not in FIELDS
                or not isinstance(quote, str)
                or not quote
                or quote not in self.question
                or not explicit(quote)
                or not any(
                    quote.strip().rstrip(".!?") in clause
                    for clause in request_clauses(self.question)
                )
            ):
                raise ValueError(
                    "An explicit lasting preference in the latest message is required.",
                )
            value = validate_value(code, value)
            if value is None:
                names_field = (
                    bool(
                        re.search(
                            r"\b(?:answers?|responses?|replies|reply|explanations?)\b",
                            quote,
                            re.IGNORECASE,
                        ),
                    )
                    if code == "answer_length"
                    else True
                )
                if (
                    not re.search(
                        r"\b(?:forget|remove|reset)\b",
                        quote,
                        re.IGNORECASE,
                    )
                    or not names_field
                    or not any(
                        re.search(pattern, quote, re.IGNORECASE)
                        for pattern in SIGNALS[code].values()
                    )
                ):
                    raise ValueError("Name the saved preference you want removed.")
            elif not any(
                supports(code, value, clause) for clause in request_clauses(quote)
            ):
                raise ValueError("The quoted request does not support that preference.")
            chat_id = self.conversation_id()
            if chat_id is None:
                raise ValueError("No active conversation.")
            changed = save_profile(
                self.service.store_id,
                self.service.manager_id,
                {code: item(value, "chat", conversation_id=str(chat_id), quote=quote)},
                engine=self.service.engine,
            )
            return json.dumps(
                {
                    "saved": True,
                    "changed": changed,
                    "preference": describe(code, value),
                    "next": "Acknowledge briefly. It can be edited or removed in Settings > Personalization.",
                },
            )
        except (KeyError, TypeError, ValueError) as exc:
            return json.dumps({"saved": False, "reason": str(exc)})


def manager_personalization(
    manager_id: str = DEMO_MANAGER_ID,
) -> PersonalizationService:
    return PersonalizationService(DEMO_STORE_ID, manager_id)
