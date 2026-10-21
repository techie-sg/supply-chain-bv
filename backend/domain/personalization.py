"""Durable preferences for how a manager receives advice."""

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from constants import PERSONALIZATION_MAX_INSTRUCTIONS
from domain.memory import PreferenceCode


@dataclass(frozen=True)
class PersonalizationField:
    name: str
    choices: dict[str, str]


FIELDS = {
    "answer_length": PersonalizationField(
        "Answer length",
        {
            "brief": "Brief",
            "balanced": "Balanced",
            "detailed": "Detailed",
        },
    ),
    "answer_order": PersonalizationField(
        "Start answers with",
        {
            "recommendation_first": "Recommendation",
            "explanation_first": "Explanation",
        },
    ),
    "comparisons": PersonalizationField(
        "When comparing options",
        {
            "best_option": "One recommendation",
            "alternatives": "Alternatives and trade-offs",
        },
    ),
    "decision_priority": PersonalizationField(
        "Consider options using",
        {
            "existing_capacity": "Existing team and capacity first",
            "lower_cost": "Lower cost first",
            "service_quality": "Service quality first",
        },
    ),
}
NOTES = "additional_instructions"
Profile = dict[str, dict[str, Any]]

SETTING_REFERENCE = re.compile(
    r"\b(?:"
    + "|".join(
        re.escape(code.value).replace("_", r"[\s_-]+")
        for code in PreferenceCode
        if code != PreferenceCode.BRIEFING
    )
    + r"|(?:order|rider|frozen|sla)\b[^.!?;\n]{0,60}\b(?:alert|threshold)"
    + r"|alert[\s_-]+(?:threshold|cooldown|schedule)"
    + r"|briefing[\s_-]+(?:views|settings))\b",
    re.IGNORECASE,
)
SETTING_VALUE = re.compile(
    r"^\s*(?:[:=<>≥≤]\s*\S|"
    r"(?:(?:is|to|at|of|above|below|over|under|set|keep|use|should|must|be)\s+)*"
    r"(?:[₹$]?\s*\d|\b(?:on|off|enabled|disabled|true|false)\b))",
    re.IGNORECASE,
)


def personal_context(text: str) -> str:
    """Exclude catalogue setting assignments from learned personal context.

    Keep free-form personal details and communication preferences. Only clauses
    naming an operational setting and assigning a value are removed.
    """

    def operational(clause: str) -> bool:
        return any(
            SETTING_VALUE.search(clause[reference.end() :])
            for reference in SETTING_REFERENCE.finditer(clause)
        )

    if not operational(text):
        return text.strip()
    kept = []
    changed = False
    for sentence in re.split(r"(?<=[.!?;])\s+|\n+", text):
        if not operational(sentence):
            kept.append(sentence)
            continue
        # A manager can supply a personal detail and a setting in one sentence.
        changed = True
        clauses = re.split(r"\s+(?:and|also)\s+|,\s*", sentence, flags=re.IGNORECASE)
        kept.extend(clause for clause in clauses if not operational(clause))
    return (
        "\n".join(part.strip() for part in kept if part.strip())
        if changed
        else text.strip()
    )


def validate_value(code: str, value: str | None) -> str | None:
    if code not in FIELDS and code != NOTES:
        raise ValueError("Unknown personalization preference.")
    if value is None or value == "":
        return None
    if code == NOTES:
        value = value.strip()
        if len(value) > PERSONALIZATION_MAX_INSTRUCTIONS:
            raise ValueError("Personal context exceeds its character limit.")
        return value or None
    if value not in FIELDS[code].choices:
        raise ValueError(f"Invalid value for {FIELDS[code].name}.")
    return value


def describe(code: str, value: str | None) -> str:
    name = FIELDS[code].name if code in FIELDS else "Additional instructions"
    label = FIELDS[code].choices[value] if code in FIELDS and value else value
    return f"{name}: {label or 'Use default'}"


class PreferenceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str
    message_index: int = Field(ge=0)
    quote: str = Field(min_length=1, max_length=600)


class PersonalizationCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    value: str
    reason: str = Field(min_length=1, max_length=400)
    evidence: list[PreferenceEvidence] = Field(min_length=1, max_length=10)
