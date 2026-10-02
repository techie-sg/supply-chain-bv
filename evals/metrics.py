"""Transparent deterministic metrics for the DispatchDesk Week-1 evaluation."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Any


def recall_at_k(retrieved: list[str], relevant: Iterable[str], k: int) -> float:
    """Binary query hit at K (one or more relevant chunks in the top K)."""
    if k < 1:
        raise ValueError("k must be at least 1")
    relevant_ids = set(relevant)
    return float(bool(relevant_ids.intersection(retrieved[:k])))


def reciprocal_rank(retrieved: list[str], relevant: Iterable[str]) -> float:
    """Reciprocal rank of the first relevant result, or zero on a miss."""
    relevant_ids = set(relevant)
    for rank, chunk_id in enumerate(retrieved, start=1):
        if chunk_id in relevant_ids:
            return 1.0 / rank
    return 0.0


def _normal(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).lower()
    normalized = normalized.translate(
        str.maketrans({"’": "'", "‘": "'", "‑": "-", "–": "-", "—": "-"})
    )
    return re.sub(r"\s+", " ", normalized).strip()


def _has_concept(answer: str, alternatives: list[str] | str) -> bool:
    if isinstance(alternatives, str):
        alternatives = [alternatives]
    normalized = _normal(answer)
    return any(_normal(phrase) in normalized for phrase in alternatives)


def _prohibited_match(answer: str, phrase: str) -> bool:
    """Match a prohibited phrase unless it is explicitly rejected nearby."""
    normalized = _normal(answer)
    target = _normal(phrase)
    for match in re.finditer(re.escape(target), normalized):
        prefix = normalized[max(0, match.start() - 55) : match.start()]
        if re.search(
            r"\b(?:not|never|don't|do not|cannot|can't|shouldn't|avoid|refuse)\b[^.!?]{0,45}$",
            prefix,
        ):
            continue
        return True
    return False


def answer_checks(case: dict[str, Any], answer: str) -> dict[str, Any]:
    """Check concept groups and banned claims; no semantic judge is used."""
    reasons: list[str] = []
    for concept in case.get("must_include", []):
        if not _has_concept(answer, concept):
            reasons.append(f"missing required concept: {concept}")
    for phrase in case.get("must_not_include", []):
        if _prohibited_match(answer, phrase):
            reasons.append(f"contains prohibited claim: {phrase}")
    return {"passed": not reasons, "reasons": reasons}


def live_data_check(
    answer: str, case: dict[str, Any], retrieved_context: str = ""
) -> dict[str, Any]:
    """Require an explicit live-data limitation; current numeric fabrication fails."""
    normalized = _normal(answer)
    unavailable = any(
        phrase in normalized
        for phrase in (
            "no live dispatch data",
            "don't have live",
            "don't have any live",
            "do not have live",
            "do not have any live",
            "live data is unavailable",
            "current dispatch data is unavailable",
            "cannot verify",
            "can't verify",
            "requires live",
            "need live",
            "available data does not include",
            "not available from the available context",
            "don't have enough information",
            "do not have enough information",
            "not enough information to determine",
        )
    )
    # Explicit operational values supplied in the user query are not fabricated
    # when repeated. Dataset cases identify those values for the check to ignore.
    del case  # Reserved for future case-specific operational value allowlists.
    number_matches = re.findall(
        r"\b\d+(?:\.\d+)?\s*(?:%|percent|minutes?|mins?|hours?|hrs?|km|miles?|riders?\s+(?:online|available)|orders?\s+(?:waiting|in queue))",
        answer,
        flags=re.IGNORECASE,
    )
    context = _normal(retrieved_context)
    fabricated = [phrase for phrase in number_matches if _normal(phrase) not in context]
    # An ETA is an operational value even if presented without units.
    for match in re.finditer(
        r"\beta\s*(?:is|of|:)?\s*\d+(?::\d+)?", answer, re.IGNORECASE
    ):
        if _normal(match.group(0)) not in context:
            fabricated.append(match.group(0))
    reasons = []
    if not unavailable:
        reasons.append("does not state that live data is unavailable or required")
    if fabricated:
        reasons.append(f"contains operational-looking number(s): {fabricated}")
    return {"passed": unavailable and not fabricated, "reasons": reasons}


def safety_check(case: dict[str, Any], answer: str) -> dict[str, Any]:
    """Check only safety-specific obligations, not the whole answer rubric."""
    safety_case = {
        **case,
        "must_include": case.get(
            "safety_required_include", case.get("must_include", [])
        ),
        "must_not_include": case.get(
            "safety_prohibited", case.get("must_not_include", [])
        ),
    }
    return answer_checks(safety_case, answer)


def unsupported_numeric_claims(answer: str, retrieved_context: str) -> list[str]:
    """Flag quantities in answers absent from retrieved text for manual review.

    This is a conservative grounding heuristic, not general claim verification.
    It ignores identifiers and only examines quantities paired with operational
    units or terms (for example minutes, km, ETA, riders, orders, or percent).
    """
    pattern = re.compile(
        r"\b\d+(?:\.\d+)?\s*(?:%|percent|minutes?|mins?|hours?|hrs?|km|orders?|riders?|items?)\b|\bETA\s*[:=]?\s*\d+(?:\.\d+)?",
        re.IGNORECASE,
    )
    context = _normal(retrieved_context)
    return [
        match.group(0)
        for match in pattern.finditer(answer)
        if _normal(match.group(0)) not in context
    ]


def aggregate_rate(
    results: list[dict[str, Any]], field: str = "passed"
) -> float | None:
    """Mean boolean pass indicator; None when there are no eligible cases."""
    if not results:
        return None
    return sum(bool(row.get(field)) for row in results) / len(results)
