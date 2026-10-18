"""The guardrail layer: NeMo Guardrails rails around every chat answer.

The rails are configured in resources/guardrails. Each one runs a policy check
from `domain.guardrails` against the question, the reply and the tool results
of the same answer; no rail calls a language model.
"""

import os
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import cache
from typing import Any

import structlog

from domain.chat import GuardrailTrace
from domain.guardrails import (
    Finding,
    Fixed,
    Refusal,
    batching_fix,
    drafted_pressure,
    eta_promise_fix,
    execution_claims_fix,
    live_status,
    preference_conflict_fix,
    rider_hours_fix,
    rider_hours_refusal,
    stale_data_fix,
    unsafe_request,
    unverified_figures,
)
from domain.tools import ToolResult
from resources import GUARDRAILS_DIR

# NeMo Guardrails reports usage to NVIDIA unless this is set before it loads.
os.environ.setdefault("NEMO_GUARDRAILS_NO_USAGE_STATS", "1")

from nemoguardrails import LLMRails, RailsConfig  # type: ignore[import-untyped]
from nemoguardrails.rails.llm.options import (  # type: ignore[import-untyped]
    RailStatus,
    RailType,
)

logger = structlog.stdlib.get_logger(__name__)


@dataclass
class _Review:
    """What the rail actions of one check found."""

    findings: list[Finding] = field(default_factory=list)
    refusal: Refusal | None = None


_review: ContextVar[_Review | None] = ContextVar("guardrail_review", default=None)


@dataclass(frozen=True)
class Guarded:
    """The text to show and what the guardrails did to it."""

    text: str
    trace: GuardrailTrace


def _blocked(refusal: Refusal | None) -> bool:
    if refusal is not None and (review := _review.get()) is not None:
        review.refusal = refusal
    return refusal is not None


def _fixed(fixed: Fixed | None) -> str | None:
    if fixed is not None and (review := _review.get()) is not None:
        review.findings.extend(fixed.findings)
    return fixed.text if fixed else None


def _flagged(finding: Finding | None) -> None:
    if finding is not None and (review := _review.get()) is not None:
        review.findings.append(finding)


def _inputs(context: dict[str, Any] | None) -> tuple[str, str, list[ToolResult]]:
    context = context or {}
    return (
        context.get("user_message") or "",
        context.get("bot_message") or "",
        context.get("tool_results") or [],
    )


async def _unsafe_request(context: dict[str, Any] | None = None) -> bool:
    question, _, _ = _inputs(context)
    return _blocked(unsafe_request(question))


async def _drafted_pressure(context: dict[str, Any] | None = None) -> bool:
    _, reply, _ = _inputs(context)
    return _blocked(drafted_pressure(reply))


async def _rider_hours_refusal(context: dict[str, Any] | None = None) -> bool:
    question, reply, results = _inputs(context)
    return _blocked(rider_hours_refusal(question, reply, live_status(results)))


async def _rider_hours_fix(context: dict[str, Any] | None = None) -> str | None:
    _, reply, results = _inputs(context)
    return _fixed(rider_hours_fix(reply, live_status(results)))


async def _batching_fix(context: dict[str, Any] | None = None) -> str | None:
    _, reply, results = _inputs(context)
    return _fixed(batching_fix(reply, live_status(results)))


async def _unverified_figures(context: dict[str, Any] | None = None) -> None:
    _, reply, results = _inputs(context)
    _flagged(unverified_figures(reply, results, (context or {}).get("references", "")))


async def _preference_conflict_fix(context: dict[str, Any] | None = None) -> str | None:
    question, reply, results = _inputs(context)
    settings = (context or {}).get("settings") or {}
    return _fixed(
        preference_conflict_fix(question, reply, live_status(results), settings),
    )


async def _eta_promise_fix(context: dict[str, Any] | None = None) -> str | None:
    _, reply, _ = _inputs(context)
    return _fixed(eta_promise_fix(reply))


async def _stale_data_fix(context: dict[str, Any] | None = None) -> str | None:
    _, reply, results = _inputs(context)
    return _fixed(stale_data_fix(reply, results))


async def _execution_claims_fix(context: dict[str, Any] | None = None) -> str | None:
    _, reply, _ = _inputs(context)
    return _fixed(execution_claims_fix(reply))


@cache
def _rails() -> LLMRails:
    rails = LLMRails(RailsConfig.from_path(str(GUARDRAILS_DIR)))
    for name, action in (
        ("unsafe_request", _unsafe_request),
        ("drafted_pressure", _drafted_pressure),
        ("rider_hours_refusal", _rider_hours_refusal),
        ("rider_hours_fix", _rider_hours_fix),
        ("batching_fix", _batching_fix),
        ("execution_claims_fix", _execution_claims_fix),
        ("unverified_figures", _unverified_figures),
        ("preference_conflict_fix", _preference_conflict_fix),
        ("eta_promise_fix", _eta_promise_fix),
        ("stale_data_fix", _stale_data_fix),
    ):
        rails.register_action(action, name=name)
    return rails


def _check(messages: list[dict[str, Any]], rail: RailType) -> tuple[Any, _Review]:
    review = _Review()
    token = _review.set(review)
    try:
        return _rails().check(messages, rail_types=[rail]), review
    finally:
        _review.reset(token)


def _outcome(
    text: str,
    status: str,
    findings: list[Finding],
    rail: str | None = None,
) -> Guarded:
    trace: GuardrailTrace = {
        "status": status,  # type: ignore[typeddict-item]
        "findings": [{"rule": item.rule, "detail": item.detail} for item in findings],
    }
    if rail:
        trace["rail"] = rail
    for item in findings:
        logger.info(
            "Guardrail triggered",
            rule=item.rule,
            outcome=status,
            detail=item.detail,
            rail=rail,
        )
    return Guarded(text, trace)


def check_question(question: str) -> Guarded | None:
    """The input rail: a refusal for an unsafe request, else None to answer it."""
    try:
        result, review = _check([{"role": "user", "content": question}], RailType.INPUT)
    except Exception:
        # A guardrail failure never blocks an answer; the output rails still run.
        logger.exception("Guardrail input check failed")
        return None
    if result.status != RailStatus.BLOCKED or review.refusal is None:
        return None
    refusal = review.refusal
    return _outcome(refusal.text, "blocked", [refusal.finding], result.rail)


def check_reply(
    question: str,
    reply: str,
    results: list[ToolResult],
    settings: dict[str, Any] | None = None,
    references: str = "",
) -> Guarded:
    """The output rails: the reply as checked against this answer's tool results.

    `settings` holds the stored values the rails test (G6); `references` is the
    other text the model saw with figures in it, such as alerts and earlier
    messages (G1).
    """
    context = {
        "tool_results": results,
        "settings": settings or {},
        "references": references,
    }
    messages: list[dict[str, Any]] = [
        {"role": "context", "content": context},
        {"role": "user", "content": question},
        {"role": "assistant", "content": reply},
    ]
    try:
        result, review = _check(messages, RailType.OUTPUT)
    except Exception:
        # Show the answer rather than lose it, marked as not checked.
        logger.exception("Guardrail output check failed")
        return Guarded(reply, {"status": "unchecked", "findings": []})
    if result.status == RailStatus.BLOCKED and review.refusal is not None:
        refusal = review.refusal
        return _outcome(refusal.text, "blocked", [refusal.finding], result.rail)
    if result.status == RailStatus.MODIFIED and review.findings:
        return _outcome(result.content, "reframed", review.findings)
    if review.findings:
        # Only flag-only rails found something; the reply is unchanged.
        return _outcome(reply, "flagged", review.findings)
    return Guarded(reply, {"status": "pass", "findings": []})
