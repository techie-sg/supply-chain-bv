"""The guardrail layer: NeMo Guardrails rails around every chat answer.

The rails are configured in resources/guardrails. Each one runs a policy check
from `domain.guardrails` against the question, the reply and the tool results
of the same answer; no rail calls a language model.
"""

import os
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import cache, wraps
from time import perf_counter
from typing import Any

import structlog
from opentelemetry.context import Context, attach, detach, get_current

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
from service.tracing import observation

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
    trace_context: Context | None = None
    errors: list[str] = field(default_factory=list)


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


def _traced_action(
    name: str,
    rule: str,
    action: Callable[..., Awaitable[Any]],
) -> Callable[..., Awaitable[Any]]:
    """Observe actual rule execution, including passes and swallowed rail errors."""

    @wraps(action)
    async def run(context: dict[str, Any] | None = None) -> Any:
        review = _review.get()
        before = len(review.findings) if review else 0
        previous_refusal = review.refusal if review else None
        # NeMo can move async actions between execution contexts. Attach the
        # public OTel context captured while the overall check span was active.
        token = (
            attach(review.trace_context) if review and review.trace_context else None
        )
        started = perf_counter()
        try:
            question, reply, results = _inputs(context)
            with observation(
                f"guardrail.{name}",
                kind="guardrail",
                input={
                    "question": question,
                    "reply": reply,
                    "tool_results": results,
                    "settings": (context or {}).get("settings", {}),
                    "references": (context or {}).get("references", ""),
                },
                metadata={"rule": rule, "action": name},
            ) as span:
                try:
                    result = await action(context=context)
                except Exception as exc:
                    error = type(exc).__name__
                    if review:
                        review.errors.append(error)
                    span.update(
                        level="ERROR",
                        status_message=error,
                        output={"outcome": "unchecked", "error_type": error},
                        metadata={
                            "outcome": "unchecked",
                            "duration_ms": round((perf_counter() - started) * 1000, 2),
                        },
                    )
                    raise
                findings = list(review.findings[before:]) if review else []
                refusal = review.refusal if review else None
                if refusal is not None and refusal is not previous_refusal:
                    findings.append(refusal.finding)
                    outcome = "blocked"
                elif findings:
                    outcome = "reframed" if result is not None else "flagged"
                else:
                    outcome = "pass"
                span.update(
                    output={
                        "outcome": outcome,
                        "result": result,
                        "findings": [
                            {"rule": item.rule, "detail": item.detail}
                            for item in findings
                        ],
                    },
                    metadata={
                        "outcome": outcome,
                        "triggered": bool(findings),
                        "duration_ms": round((perf_counter() - started) * 1000, 2),
                    },
                )
                return result
        finally:
            if token is not None:
                detach(token)

    return run


@cache
def _rails() -> LLMRails:
    rails = LLMRails(RailsConfig.from_path(str(GUARDRAILS_DIR)))
    for name, rule, action in (
        ("unsafe_request", "G3", _unsafe_request),
        ("drafted_pressure", "G3", _drafted_pressure),
        ("rider_hours_refusal", "G4", _rider_hours_refusal),
        ("rider_hours_fix", "G4", _rider_hours_fix),
        ("batching_fix", "G7", _batching_fix),
        ("execution_claims_fix", "G5", _execution_claims_fix),
        ("unverified_figures", "G1", _unverified_figures),
        ("preference_conflict_fix", "G6", _preference_conflict_fix),
        ("eta_promise_fix", "G2", _eta_promise_fix),
        ("stale_data_fix", "G8", _stale_data_fix),
    ):
        rails.register_action(_traced_action(name, rule, action), name=name)
    return rails


def _check(messages: list[dict[str, Any]], rail: RailType) -> tuple[Any, _Review]:
    started = perf_counter()
    with observation(
        f"guardrails.{rail.value}",
        kind="guardrail",
        input=messages,
        metadata={"stage": rail.value},
    ) as span:
        review = _Review(trace_context=get_current())
        token = _review.set(review)
        try:
            try:
                result = _rails().check(messages, rail_types=[rail])
            except Exception as exc:
                error = type(exc).__name__
                span.update(
                    level="ERROR",
                    status_message=error,
                    output={"outcome": "unchecked", "error_type": error},
                    metadata={
                        "outcome": "unchecked",
                        "duration_ms": round((perf_counter() - started) * 1000, 2),
                    },
                )
                raise
            findings = list(review.findings)
            if result.status == RailStatus.BLOCKED and review.refusal is not None:
                outcome = "blocked"
                text = review.refusal.text
                findings = [review.refusal.finding]
            else:
                text = result.content
                outcome = (
                    "reframed"
                    if result.status == RailStatus.MODIFIED and findings
                    else "flagged"
                    if findings
                    else "pass"
                )
            span.update(
                output={
                    "outcome": "unchecked" if review.errors else outcome,
                    "text": text,
                    "findings": [
                        {"rule": item.rule, "detail": item.detail} for item in findings
                    ],
                    "errors": review.errors,
                },
                metadata={
                    "outcome": "unchecked" if review.errors else outcome,
                    "triggered": bool(findings),
                    "duration_ms": round((perf_counter() - started) * 1000, 2),
                },
                **(
                    {"level": "ERROR", "status_message": ", ".join(review.errors)}
                    if review.errors
                    else {}
                ),
            )
            return result, review
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
