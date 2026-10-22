import json
import math
import re
from collections.abc import Sequence
from contextvars import ContextVar
from datetime import datetime
from functools import partial
from hashlib import sha256
from time import perf_counter
from typing import Any, Literal, Protocol

import requests
import structlog
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from config import get_settings
from constants import NO_GUIDANCE_ANSWER, TIMEZONE
from domain.chat import ChatMessage, RetrievalTrace, ToolCallTrace
from domain.tools import PlaybookSearchInput, Tool, ToolResult
from queries.vector_store import retrieve
from resources import PROMPTS
from service.cache import Cache, cache_key, retrieval_cache
from service.embedding_service import EmbeddingService
from service.factory import create_embedding_service, create_llm_service
from service.llm_service import LLMService
from service.tools import traced_tools
from service.tracing import observation

logger = structlog.stdlib.get_logger(__name__)


PROMPT_PATH = PROMPTS / "dispatch_manager_system.md"
POLICY_CITATION = re.compile(r"\bDD-[A-Z0-9]+-\d{3}(?:#[\w-]+)?\b")
POLICY_TOPIC = re.compile(
    r"\b(?:polic(?:y|ies)|playbook|SOPs?|rules?)\b",
    re.IGNORECASE,
)
POLICY_LANGUAGE = re.compile(
    r"\b(?:allowed|permitted|eligible|eligibility|prohibited|forbidden|mandatory)\b"
    r"|\b(?:polic(?:y|ies)|playbook|SOPs?|rules?)\s+(?:says?|states?|requires?|limits?|allows?|permits?|forbids?|mandates?|is|are)\b"
    r"|\b(?:according to|under)\s+(?:the\s+)?(?:store\s+)?(?:polic(?:y|ies)|playbook|SOPs?|rules?)\b"
    r"|\b(?:batch(?:ing|ed)?|bundle|combine|pair|group)\b.{0,80}\b(?:orders?|deliveries|frozen|items?|them|those)\b"
    r"|\b(?:orders?|deliveries|frozen|items?)\b.{0,80}\b(?:batch(?:ing|ed)?|bundle|combine|paired|grouped)\b"
    r"|\b(?:should|must|need|recommend|suggest|consider|propose)\b.{0,80}\b(?:assign|dispatch|batch|deliver|riders?|drivers?|orders?|incentive|radius|zones?|routes?|breaks?|shifts?|SLA)\b"
    r"|\b(?:assign|reassign|dispatch|batch|call in|activate|shrink|pause|prioriti[sz]e|penalize|dock|deduct|speed|skip|extend|offer|raise|increase|reduce|lower)\b.{0,80}\b(?:riders?|drivers?|orders?|deliveries|pay|incentive|radius|zones?|routes?|breaks?|shifts?|them|those)\b"
    r"|\b(?:frozen|ice[- ]cream|cold[- ]chain)\b.{0,80}\b(?:separate|single[- ]drop|deliver|batch|combine|pair|must|need|require)\b"
    r"|\b(?:dispatch|batch|assign|reassign|activate|shrink|pause)\s+(?:now|immediately|them|those|it)\b"
    r"|\b(?:speed up|skip breaks?|work overtime|jump (?:red )?(?:lights|signals)|dock (?:their )?pay)\b",
    re.IGNORECASE | re.DOTALL,
)
CLARIFICATION = re.compile(
    r"^(?:do you mean|are you asking|what do you mean|which (?:policy|rule|part|topic)|what would you like|how can I help|(?:could|can) you clarify|please clarify)\b",
    re.IGNORECASE,
)
CAPABILITY = re.compile(
    r"^I can (?:help(?: you)?|explain|find|look up|review|summari[sz]e|clarify)\b",
    re.IGNORECASE,
)


def _requires_policy(
    question: str,
    answer: str,
    history: Sequence[ChatMessage],
) -> bool:
    """Conservative English claim check, not semantic proof of every possible advice."""
    sentences = re.split(r"(?<=[.!?])\s+|\n+", answer.strip())
    clarifications = [
        sentence
        for sentence in sentences
        if sentence.endswith("?") and CLARIFICATION.search(sentence)
    ]
    # Clarifying a possible action is not recommending it. Check every other
    # sentence so adding a question cannot hide an unsupported policy claim.
    claims = "\n".join(
        sentence for sentence in sentences if sentence not in clarifications
    )
    if POLICY_LANGUAGE.search(claims):
        return True
    if clarifications or all(CAPABILITY.search(sentence) for sentence in sentences):
        return False
    if POLICY_TOPIC.search(question) or POLICY_LANGUAGE.search(question):
        return True
    # A request about presentation or identity must not inherit an older policy
    # topic. The answer itself is still checked above, including mixed advice.
    if re.search(
        r"\b(?:concise|brief|short|long|simple|technical|jargon|bullet|format|my name|my e-?mail|about me|who am i)\b",
        question,
        re.IGNORECASE,
    ):
        return False
    followup = re.fullmatch(
        r"\s*(?:what|why|how|really|what do you mean|tell me more|explain more)[\s.!?]*",
        question,
        re.IGNORECASE,
    ) or re.search(r"\b(?:that|those|them|it|what about)\b", question, re.IGNORECASE)
    return bool(
        followup
        and any(
            POLICY_TOPIC.search(message["content"])
            or POLICY_LANGUAGE.search(message["content"])
            for message in history[-2:]
        ),
    )


# Search activity for this answer, read once by the caller building its trace.
_retrieval_trace: ContextVar[RetrievalTrace | None] = ContextVar(
    "retrieval_trace",
    default=None,
)


def pop_retrieval_trace() -> RetrievalTrace | None:
    """The last retrieval's {cache, chunks, ms}, cleared so it is never reused."""
    trace = _retrieval_trace.get()
    _retrieval_trace.set(None)
    return trace


def retrieval_query(
    question: str,
    history: Sequence[ChatMessage] | None = None,
) -> str:
    """Baseline query for offline retrieval evals, independent of agent search."""
    if not history:
        return question
    recent_exchange = "\n".join(
        f"{message['role']}: {message['content']}" for message in history[-2:]
    )
    return f"{recent_exchange}\nFollow-up question: {question}"


class PreferenceContext(Protocol):
    """The manager's settings, shown to the model so its answers apply them."""

    def prompt_block(self) -> str: ...


def _cell(value: Any) -> str:
    if value is None:
        return "Unknown"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _table(rows: list[dict], fields: tuple[tuple[str, str], ...]) -> str:
    return "\n".join(
        [
            "| " + " | ".join(label for _, label in fields) + " |",
            "| " + " | ".join("---" for _ in fields) + " |",
            *[
                "| " + " | ".join(_cell(row.get(key)) for key, _ in fields) + " |"
                for row in rows
            ],
        ],
    )


def _numeric_fields(data: dict, fields: tuple[tuple[str, str], ...]) -> list[str]:
    return [
        f"- **{label}:** {value}"
        for key, label in fields
        if isinstance(value := data.get(key), (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    ]


def _live_facts(data: dict, question: str) -> str | None:
    """Render known tool fields; never reuse model explanations or recommendations."""
    try:
        when = datetime.fromisoformat(data["as_of"])
        if when.tzinfo is None:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    queue, summary = data.get("queue"), data.get("summary")
    if not isinstance(queue, dict) or not isinstance(summary, dict):
        return None
    lines = _numeric_fields(
        queue,
        (("open_orders", "Open orders"), ("packed_waiting", "Packed and waiting")),
    )
    lines += _numeric_fields(
        summary,
        (
            ("available_riders", "Available riders"),
            ("riders_returning_within_10_min", "Riders returning within 10 minutes"),
        ),
    )
    if not lines:
        return None
    heading = f"**Dispatch facts as of {when.astimezone(TIMEZONE):%d %b %Y, %H:%M}.**"
    if data.get("stale"):
        heading += " This saved snapshot is stale and may be out of date."
    blocks = [heading, "\n".join(lines)]
    for term, rows, fields in (
        (
            r"\borders?\b",
            queue.get("orders"),
            (
                ("order_id", "Order"),
                ("status", "Status"),
                ("zone_id", "Zone"),
                ("item_count", "Items"),
                ("has_frozen_items", "Frozen"),
                ("assigned_rider_id", "Rider"),
            ),
        ),
        (
            r"\b(?:riders?|drivers?)\b",
            data.get("riders"),
            (
                ("rider_id", "Rider ID"),
                ("name", "Name"),
                ("status", "Status"),
                ("hours_on_shift", "Hours on shift"),
                ("minutes_since_last_break", "Minutes since break"),
            ),
        ),
    ):
        if re.search(term, question, re.IGNORECASE) and isinstance(rows, list) and rows:
            blocks.append(
                _table([row for row in rows if isinstance(row, dict)], fields),
            )
    if queue.get("orders_truncated") and re.search(
        r"\borders?\b",
        question,
        re.IGNORECASE,
    ):
        blocks.append("The tool returned only part of the order list.")
    return "\n\n".join(blocks)


def _metric_facts(data: dict, question: str) -> str | None:
    period, summary = data.get("period"), data.get("period_summary")
    if not isinstance(period, dict) or not isinstance(summary, dict):
        return None
    lines = _numeric_fields(
        summary,
        (
            ("total_orders", "Orders"),
            ("sla_10min_pct", "SLA compliance (%)"),
            ("avg_pick_pack_min", "Average pick/pack (min)"),
            ("avg_rider_wait_min", "Average rider wait (min)"),
            ("avg_ride_min", "Average ride (min)"),
            ("avg_riders_online", "Average riders online"),
        ),
    )
    if not lines:
        return None
    heading = f"**Historical facts: {_cell(period.get('date'))}, {_cell(period.get('start_hour'))}:00 to {_cell(period.get('end_hour'))}:00.**"
    blocks = [heading, "\n".join(lines)]
    if period.get("hours_missing"):
        blocks.append(
            "Missing hours: "
            + ", ".join(_cell(hour) for hour in period["hours_missing"])
            + ".",
        )
    rows = data.get("hourly")
    if (
        re.search(r"\bhour(?:ly|s)?\b", question, re.IGNORECASE)
        and isinstance(rows, list)
        and rows
    ):
        blocks.append(
            _table(
                [row for row in rows if isinstance(row, dict)],
                (
                    ("hour", "Hour"),
                    ("orders", "Orders"),
                    ("sla_10min_pct", "SLA (%)"),
                    ("avg_pick_pack_min", "Pick/pack (min)"),
                    ("avg_rider_wait_min", "Rider wait (min)"),
                    ("avg_ride_min", "Ride (min)"),
                ),
            ),
        )
    return "\n\n".join(blocks)


def _safe_fallback(
    question: str,
    results: list[ToolResult],
) -> str:
    blocks = []
    for result in results:
        data = result["result"]
        if data.get("error"):
            continue
        if result["tool"] == "get_live_dispatch_status":
            facts = _live_facts(data, question)
        elif result["tool"] == "get_delivery_metrics":
            facts = _metric_facts(data, question)
        elif result["tool"] == "propose_setting_change":
            if data.get("status") == "proposed":
                facts = f"Proposed setting change: {_cell(data.get('setting'))}, {_cell(data.get('from'))} → {_cell(data.get('to'))}. Nothing is saved yet. Press Confirm below to save, or Cancel."
            elif data.get("status") == "rejected":
                facts = f"The setting change was rejected: {_cell(data.get('reason'))}. Nothing was saved."
            else:
                facts = None
        else:
            facts = None
        if facts and facts not in blocks:
            blocks.append(facts)
    if blocks:
        if any(result["tool"] == "search_playbook" for result in results):
            blocks.append(
                "I can report these facts, but I can't verify a policy recommendation for this reply.",
            )
        return "\n\n".join(blocks)
    return NO_GUIDANCE_ANSWER


class RAGService:
    def __init__(
        self,
        embedding_service: EmbeddingService,
        llm_service: LLMService,
        cache: Cache | None = None,
    ) -> None:
        self.embedding_service = embedding_service
        self.llm_service = llm_service
        self.cache = cache or retrieval_cache()

    def _retrieve(self, query: str, top_k: int) -> list[dict[str, Any]]:
        """Chunks for the query, from the cache when the same one was just asked."""
        with observation(
            "playbook.retrieve",
            kind="retriever",
            input={"query": query, "top_k": top_k},
            model=self.embedding_service.model,
            metadata={"cache": "lookup" if self.cache.enabled else "off"},
        ) as span:
            started = perf_counter()
            # Model and adapters decide the vector space, so they are part of the key.
            key = cache_key(
                self.embedding_service.model,
                getattr(self.embedding_service, "task_adapters", None),
                top_k,
                " ".join(query.casefold().split()),
            )
            state: Literal["hit", "miss", "off"]
            cached = self.cache.get(key) if self.cache.enabled else None
            if cached is not None:
                state = "hit"
                span.update(metadata={"cache": state})
                results = list(cached)
            else:
                state = "miss" if self.cache.enabled else "off"
                span.update(metadata={"cache": state})
                embedding = self.embedding_service.embed_query(query)
                results = retrieve(query_embedding=embedding, match_count=top_k)
                if results and self.cache.enabled:
                    self.cache.set(key, tuple(results))
            span.update(
                output=results,
                metadata={
                    "cache": state,
                    "chunks": [result["chunk_id"] for result in results],
                },
            )
            trace: RetrievalTrace = {
                "cache": state,
                "chunks": [result["chunk_id"] for result in results],
                "ms": round((perf_counter() - started) * 1000, 1),
            }
            previous = _retrieval_trace.get()
            if previous is not None:
                trace["chunks"] = list(
                    dict.fromkeys([*previous["chunks"], *trace["chunks"]]),
                )
                trace["ms"] = round(previous["ms"] + trace["ms"], 1)
                if previous["cache"] == "miss":
                    trace["cache"] = "miss"
            _retrieval_trace.set(trace)
            return results

    def search_tool(self, top_k: int = 3) -> Tool:
        """Policy retrieval through the same tool contract as live dispatch."""
        if top_k < 1:
            raise ValueError("top_k must be positive")
        return Tool(
            name="search_playbook",
            description=(
                "Look up dispatch policies, limits and permitted actions. "
                "Required before policy advice or operational recommendations. "
                "For policy-only questions, such as whether riders may jump "
                "red lights, use this tool without fetching orders or riders. "
                "For decisions about specific orders or riders, fetch their "
                "facts first, then search applicable rules. Skip for facts-only "
                "reports, settings and conversation. Cite returned chunk IDs; "
                "make at most one alternate search if genuinely needed."
            ),
            parameters=PlaybookSearchInput.model_json_schema(),
            run=partial(self._search, top_k=top_k),
        )

    def _search(self, arguments: dict[str, Any], *, top_k: int) -> str:
        try:
            query = PlaybookSearchInput.model_validate(arguments).query
        except ValidationError as exc:
            return json.dumps(
                {
                    "error": {
                        "code": "INVALID_INPUT",
                        "message": "Supply a non-empty, self-contained query.",
                        "details": exc.errors(include_input=False, include_url=False),
                    },
                },
            )
        try:
            results = self._retrieve(query, top_k)
        except (
            requests.RequestException,
            SQLAlchemyError,
            RuntimeError,
            ValueError,
        ) as exc:
            logger.warning("Playbook search failed", error_type=type(exc).__name__)
            return json.dumps(
                {
                    "error": {
                        "code": "GUIDANCE_UNAVAILABLE",
                        "message": (
                            "The playbook could not be reached. Do not invent "
                            "policy or recommend actions without it. You may "
                            "still fetch and report operational facts."
                        ),
                    },
                    "sources": [],
                },
            )
        if not results:
            return json.dumps(
                {
                    "error": {"code": "NO_GUIDANCE", "message": NO_GUIDANCE_ANSWER},
                    "sources": [],
                },
            )
        return json.dumps({"query": query, "sources": results})

    def answer_question(
        self,
        question: str,
        top_k: int = 3,
        history: Sequence[ChatMessage] | None = None,
        preferences: PreferenceContext | None = None,
        summary: str | None = None,
        handover: str | None = None,
        personalization: str | None = None,
        tools: Sequence[Tool] = (),
        alerts: str | None = None,
        tool_trace: list[ToolCallTrace] | None = None,
        tool_results: list[ToolResult] | None = None,
    ) -> str:
        """Answer with on-demand policy retrieval and the other local tools.

        With preferences, the model sees the manager's settings and applies them.
        With tools, the model may call them before answering; the settings tool
        only proposes changes, which the manager confirms outside the model.
        `summary` stands in for older messages that `history` no longer holds.
        `handover` is the latest shift's handover notes; `personalization` contains
        durable saved personal context and preferences. The model chooses search queries
        from the current request, history and any facts it fetched.
        `alerts` lists the manager's alerts firing now, computed from live data.
        """
        search = self.search_tool(top_k)
        started = perf_counter()
        _retrieval_trace.set(None)
        results = tool_results if tool_results is not None else []
        result_start = len(results)
        user_message = f"Question: {question}"
        if history:
            user_message = (
                "The manager is continuing the conversation above. Interpret the "
                "latest message using the previous user and assistant messages. "
                "Resolve references before searching the playbook. Earlier "
                "policy citations are not evidence for this answer; search for "
                "the relevant policy again when needed.\n\n"
                f"Question: {question}"
            )
        if summary:
            user_message = (
                "<conversation_summary>\nEarlier in this chat (a summary; it may "
                f"omit details):\n{summary}\n</conversation_summary>\n\n{user_message}"
            )
        if personalization:
            user_message = (
                "<personalization>\nSaved personal context and preferences. The current "
                "request takes precedence. Policy, operational settings and "
                f"live facts constrain all advice.\n{personalization}\n</personalization>\n\n"
                f"{user_message}"
            )
        if handover:
            user_message = (
                f"<handover_notes>\n{handover}\n</handover_notes>\n\n{user_message}"
            )
        if alerts:
            user_message = f"{alerts}\n\n{user_message}"
        if preferences is not None:
            with observation("context.settings", kind="span") as context_span:
                preference_block = preferences.prompt_block()
                context_span.update(
                    output=preference_block,
                    metadata={"sha256": sha256(preference_block.encode()).hexdigest()},
                )
            user_message = f"{preference_block}\n\n{user_message}"
        system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
        source_digest = sha256(system_prompt.encode()).hexdigest()
        today = datetime.now(TIMEZONE).date().isoformat()
        system_prompt += f"\n\nToday's date is {today} (Asia/Kolkata)."
        with observation(
            "rag.answer",
            kind="chain",
            input={"question": question},
            metadata={
                "top_k": top_k,
                "prompt_source": PROMPT_PATH.name,
                "prompt_source_sha256": source_digest,
                "system_prompt_sha256": sha256(system_prompt.encode()).hexdigest(),
                "user_message_sha256": sha256(user_message.encode()).hexdigest(),
            },
        ) as span:
            model_answer = self.llm_service.generate_with_tools(
                system_prompt=system_prompt,
                user_message=user_message,
                tools=traced_tools(
                    [search, *tools],
                    tool_trace if tool_trace is not None else [],
                    results,
                ),
                history=history,
            )
            current_results = results[result_start:]
            answer = self._check_sources(
                model_answer,
                current_results,
                question=question,
                history=history,
            )
            retrieval_trace = _retrieval_trace.get()
            source_check_changed = answer != model_answer
            no_guidance = answer == NO_GUIDANCE_ANSWER
            span.update(
                output={"model_answer": model_answer, "answer": answer},
                metadata={
                    "source_check_changed": source_check_changed,
                    "no_guidance_answer": no_guidance,
                    "tools_called": [result["tool"] for result in current_results],
                    "retrieval": retrieval_trace,
                },
                **(
                    {
                        "level": "WARNING",
                        "status_message": (
                            "SOURCE_CHECK_REJECTED"
                            if source_check_changed
                            else "NO_GUIDANCE"
                        ),
                    }
                    if source_check_changed or no_guidance
                    else {}
                ),
            )
            logger.info(
                "RAG answer completed",
                retrieved_chunks=len(retrieval_trace["chunks"])
                if retrieval_trace
                else 0,
                retrieval_cache=retrieval_trace["cache"] if retrieval_trace else None,
                duration_ms=round((perf_counter() - started) * 1000, 2),
            )
            return answer

    @staticmethod
    def _check_sources(
        answer: str,
        results: list[ToolResult],
        *,
        question: str = "",
        history: Sequence[ChatMessage] | None = None,
    ) -> str:
        """Require current citations for policy advice; preserve ordinary conversation.

        Operational claim detection is a conservative English heuristic. It
        supplements the prompt and domain guardrails; it is not a semantic
        entailment checker. Rejected mixed replies retain only verified facts.
        """
        sources = {
            source["chunk_id"]
            for result in results
            if result["tool"] == "search_playbook" and not result["result"].get("error")
            for source in result["result"].get("sources", [])
            if isinstance(source, dict)
            and isinstance(source.get("chunk_id"), str)
            and source["chunk_id"]
            and isinstance(source.get("content"), str)
            and source["content"].strip()
        }
        allowed = sources | {source.split("#", 1)[0] for source in sources}
        citations = POLICY_CITATION.findall(answer)
        if any(citation not in allowed for citation in citations):
            logger.warning("Answer cited policy not retrieved in this turn")
            return _safe_fallback(question, results)
        if citations:
            return answer
        if any(result["tool"] == "propose_setting_change" for result in results):
            return _safe_fallback(question, results)
        if not any(
            result["tool"] == "search_playbook" for result in results
        ) and not _requires_policy(question, answer, history or []):
            return answer
        logger.warning(
            "Answer lacks verified current-turn policy citations; using safe fallback",
            playbook_search_attempted=any(
                result["tool"] == "search_playbook" for result in results
            ),
        )
        return _safe_fallback(question, results)


def answer_question(
    question: str,
    top_k: int = 3,
    history: Sequence[ChatMessage] | None = None,
    preferences: PreferenceContext | None = None,
    summary: str | None = None,
    handover: str | None = None,
    personalization: str | None = None,
    tools: Sequence[Tool] = (),
    alerts: str | None = None,
    tool_trace: list[ToolCallTrace] | None = None,
    tool_results: list[ToolResult] | None = None,
) -> str:
    """UI entry point composing the configured services."""
    settings = get_settings()
    service = RAGService(
        embedding_service=create_embedding_service(settings),
        llm_service=create_llm_service(settings),
    )
    return service.answer_question(
        question,
        top_k,
        history=history,
        preferences=preferences,
        summary=summary,
        handover=handover,
        personalization=personalization,
        tools=tools,
        alerts=alerts,
        tool_trace=tool_trace,
        tool_results=tool_results,
    )
