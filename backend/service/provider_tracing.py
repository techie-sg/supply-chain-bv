"""Provider telemetry normalization without credentials, estimates or extra calls."""

import math
import re
from collections.abc import Mapping
from typing import Any

_RATE_HEADERS = {
    "retry-after",
    "x-ratelimit-limit-requests",
    "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-remaining-tokens",
    "x-ratelimit-reset-requests",
    "x-ratelimit-reset-tokens",
}
_SAFE_HEADER = re.compile(r"[A-Za-z0-9,.: +/_-]{1,200}\Z")
_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9._:/-]{1,200}\Z")
_TOKEN_FIELDS = {
    "input_tokens",
    "output_tokens",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
}
_DETAIL_FIELDS = {
    "cached_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "audio_tokens",
    "video_tokens",
    "image_tokens",
    "accepted_prediction_tokens",
    "rejected_prediction_tokens",
    "cache_read",
    "cache_creation",
    "reasoning",
    "audio",
    "text",
}


def http_metadata(response: Any) -> dict[str, Any]:
    """Only export documented rate-limit headers and a syntactically safe request ID."""
    result: dict[str, Any] = {}
    status = getattr(response, "status_code", None)
    if isinstance(status, int) and not isinstance(status, bool):
        result["http_status"] = status
    headers = getattr(response, "headers", None)
    if not isinstance(headers, Mapping):
        return result
    rate_limits = {}
    for name, value in headers.items():
        name = str(name).lower()
        if not isinstance(value, str):
            continue
        if name in _RATE_HEADERS and _SAFE_HEADER.fullmatch(value):
            rate_limits[name] = value
        elif name == "x-request-id" and _SAFE_REQUEST_ID.fullmatch(value):
            result["provider_request_id"] = value
    if rate_limits:
        result["rate_limit"] = rate_limits
    return result


def _count(value: Any) -> int | None:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else None
    )


def _details(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    return {
        name: count
        for name, value in value.items()
        if name in _DETAIL_FIELDS and (count := _count(value)) is not None
    }


def _cost(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        amount = float(value)
    except OverflowError:
        return None
    return amount if math.isfinite(amount) and amount >= 0 else None


def usage_fields(value: Any, *, provider: str) -> dict[str, Any]:
    """Use mutually exclusive Langfuse buckets; retain inclusive provider counts separately.

    Cache reads/writes are subsets of OpenRouter/Groq input; reasoning is a
    subset of output. Other modality details stay in metadata because they can
    overlap cache buckets. Missing costs stay unknown: Langfuse may independently
    estimate them using its model prices, but this application invents no prices.
    """
    usage = value if isinstance(value, Mapping) else {}
    reported = {
        name: count
        for name, raw in usage.items()
        if name in _TOKEN_FIELDS and (count := _count(raw)) is not None
    }
    prompt_details = _details(usage.get("prompt_tokens_details"))
    completion_details = _details(usage.get("completion_tokens_details"))
    input_details = _details(usage.get("input_token_details"))
    output_details = _details(usage.get("output_token_details"))
    breakdown = {
        name: details
        for name, details in (
            ("prompt_tokens_details", prompt_details),
            ("completion_tokens_details", completion_details),
            ("input_token_details", input_details),
            ("output_token_details", output_details),
        )
        if details
    }
    metadata: dict[str, Any] = {
        "usage_source": "provider_reported" if reported else "unknown",
        "provider_usage": {**reported, **breakdown},
        "usage_buckets": "exclusive",
        "cost_source": "unknown",
    }
    normalized: dict[str, int] = {}
    inputs = reported.get("input_tokens", reported.get("prompt_tokens"))
    outputs = reported.get("output_tokens", reported.get("completion_tokens"))
    if provider == "jina" and inputs is None:
        inputs = reported.get("total_tokens")
    for name, total, parts in (
        (
            "input",
            inputs,
            {
                "input_cached_tokens": input_details.get(
                    "cache_read",
                    prompt_details.get("cached_tokens"),
                ),
                "input_cache_creation": input_details.get(
                    "cache_creation",
                    prompt_details.get("cache_write_tokens"),
                ),
            },
        ),
        (
            "output",
            outputs,
            {
                "output_reasoning_tokens": output_details.get(
                    "reasoning",
                    completion_details.get("reasoning_tokens"),
                ),
            },
        ),
    ):
        if total is None:
            continue
        buckets = {key: count for key, count in parts.items() if count is not None}
        if sum(buckets.values()) <= total:
            normalized[name] = total - sum(buckets.values())
            normalized.update(buckets)
        else:
            normalized[name] = total
            metadata["usage_inconsistent"] = True
    if "total_tokens" in reported:
        normalized["total"] = reported["total_tokens"]
        if (
            inputs is not None
            and outputs is not None
            and inputs + outputs != reported["total_tokens"]
        ):
            metadata["usage_inconsistent"] = True
    fields: dict[str, Any] = {"usage_details": normalized, "metadata": metadata}
    cost = _cost(usage.get("cost"))
    if cost is not None:
        fields["cost_details"] = {"total": cost}
        metadata["cost_source"] = "provider_reported"
    costs = usage.get("cost_details")
    if isinstance(costs, Mapping):
        metadata["provider_cost_breakdown"] = {
            name: amount
            for name, value in costs.items()
            if name
            in {
                "upstream_inference_cost",
                "upstream_inference_prompt_cost",
                "upstream_inference_completions_cost",
            }
            and (amount := _cost(value)) is not None
        }
    return fields
