"""Durable preferences for how a manager receives advice."""

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from constants import PERSONALIZATION_MAX_INSTRUCTIONS


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
