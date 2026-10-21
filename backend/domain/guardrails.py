"""Policy checks on a question and its reply, against the answer's tool results.

Pure functions with no model call; `service.guardrails` runs them as NeMo
Guardrails actions. Rule IDs (G1 to G8) are those in docs/guardrails.md.
"""

import re
from dataclasses import dataclass
from typing import Any

from domain.tools import ToolResult

MAX_SHIFT_HOURS = 9
BREAK_DUE_MIN = 240
MAX_BATCH_ORDERS = 2
MAX_BATCH_ITEMS = 15
MAX_BATCH_AGE_SEC = 480

ALTERNATIVES = (
    "**What I can propose instead (each needs your approval):**\n"
    "- Call in standby riders.\n"
    "- Batch eligible orders under the batching rules; never frozen items.\n"
    "- Temporarily shrink the serviceability radius or pause the outer zone.\n"
    "- Show customers an honest, longer ETA.\n"
    "- Request extra riders from regional ops."
)
EXECUTION_NOTE = (
    "Guardrail note: nothing was carried out. DispatchDesk only proposes "
    "actions; each one needs your approval."
)

RIDERS = r"\b(?:riders?|them|they|him|her|boys|guys|partners|team)\b"
UNSAFE_REQUESTS = {
    "pressure": (
        re.compile(
            r"\b(?:speed(?:ing)? up|ride faster|drive faster|go faster|move faster"
            r"|hurry|jump(?:ing)? (?:the )?(?:signals?|lights?|reds?)"
            r"|break(?:ing)? (?:the )?(?:traffic )?rules|no matter what|at any cost"
            r"|whatever it takes)\b",
            re.IGNORECASE,
        ),
        (
            "Riders are never asked, pressured or incentivized to speed, jump signals "
            "or ride unsafely to hit a delivery time, and the SLA does not override this"
        ),
    ),
    "penalty": (
        re.compile(
            r"\b(?:dock|deduct|cut|withhold|reduce)\w*\b.{0,30}"
            r"\b(?:pay|wages?|salary|earnings|incentives?|payouts?)\b"
            r"|\bpenali[sz]\w*|\bpunish\w*|\bfine (?:them|him|her|riders?)\b",
            re.IGNORECASE,
        ),
        (
            "Riders are never penalized (pay deductions, warnings or rating impact) "
            "for delays caused by weather, traffic or safety decisions"
        ),
    ),
    "break": (
        re.compile(
            r"\b(?:skip|cancel|delay|postpone)\w* (?:(?:their|his|her|the|a) )?breaks?\b",
            re.IGNORECASE,
        ),
        (
            "A rider must get at least a 15-minute break after every 4 hours without "
            "one, and a break is never skipped or delayed"
        ),
    ),
}

# A question about the policy ("Can riders jump red lights?"), not a request;
# the model answers it from the playbook.
POLICY_QUESTION = re.compile(
    r"^\s*(?:can|could|may|should|is|are|do|does|why|what|how|when|who|which)\b"
    r"(?!\s+you\b)[^?]*\?\s*$",
    re.IGNORECASE,
)
NEGATION = re.compile(
    r"\b(?:not|never|no|cannot|unable|avoid|exclude[sd]?|ineligible|instead)\b"
    r"|n't\b",
    re.IGNORECASE,
)
DRAFTED_PRESSURE = re.compile(
    r"\b(?:ride|drive|go|move) faster\b|\bspeed up\b|\bhurry\b|\bno excuses\b"
    r"|\bjump (?:the )?(?:signals?|lights?)\b"
    r"|\b(?:will|would|shall) be (?:docked|deducted|penali[sz]ed|fined|cut)\b"
    r"|\b(?:dock|deduct|cut)\w* (?:their|his|her|your) "
    r"(?:pay|wages?|salary|earnings|incentives?)\b"
    r"|\bskip (?:(?:their|his|her|your|the) )?breaks?\b",
    re.IGNORECASE,
)
ASSIGN = re.compile(
    r"\b(?:assign|reassign|dispatch|send|use|allocate|schedule|extend)\w*\b"
    r"|\bkeep\w*\b.{0,30}\b(?:on|till|until|working|going|longer|late)\b"
    r"|\bstay\w*\b.{0,15}\b(?:on|late|till|until|longer)\b",
    re.IGNORECASE,
)
KEEP_REQUEST = re.compile(
    r"\b(?:keep|extend|hold|stay|work|continue)\w*\b.{0,40}"
    r"\b(?:on|late|longer|till|until|past|beyond|extra|overtime|close)\b"
    r"|\bovertime\b|\bdouble shift\b",
    re.IGNORECASE,
)
BATCH = re.compile(r"\b(?:batch|pair|combin)\w*", re.IGNORECASE)
CONDITIONAL = re.compile(
    r"\b(?:detour|conditional|if|once|pending|confirm\w*|verif\w*|check\w*)\b",
    re.IGNORECASE,
)
ORDER_ID = re.compile(r"\bORD-\d+-\d+\b")
CLAIM = re.compile(
    r"\b(?:I|I've|I have|we|we've|we have)\s+(?:just\s+|already\s+|now\s+)?"
    r"(assigned|reassigned|dispatched|batched|sent|messaged|contacted|called"
    r"|notified|activated|paused|scheduled|informed|alerted)\b",
    re.IGNORECASE,
)
BASE_VERB = {
    "assigned": "assign",
    "reassigned": "reassign",
    "dispatched": "dispatch",
    "batched": "batch",
    "sent": "send",
    "messaged": "message",
    "contacted": "contact",
    "called": "call",
    "notified": "notify",
    "activated": "activate",
    "paused": "pause",
    "scheduled": "schedule",
    "informed": "inform",
    "alerted": "alert",
}
SENTENCE = re.compile(r"[^\n]+?(?:[.!?](?=\s)|$)", re.MULTILINE)


@dataclass(frozen=True)
class Finding:
    rule: str
    detail: str


@dataclass(frozen=True)
class Fixed:
    """A reply with the parts that broke a rule rewritten, and why."""

    text: str
    findings: list[Finding]


@dataclass(frozen=True)
class Refusal:
    """A reply replaced by a refusal built in code."""

    text: str
    finding: Finding


def live_status(results: list[ToolResult]) -> dict[str, Any] | None:
    """The latest successful live-status result of this answer."""
    for item in reversed(results):
        if item["tool"] == "get_live_dispatch_status" and "error" not in item["result"]:
            return item["result"]
    return None


def _sentences(text: str) -> list[re.Match[str]]:
    return list(SENTENCE.finditer(text))


def _refusal(reasons: list[str], policy: str) -> str:
    return f"I can't help with that. {'. '.join(reasons)} ({policy}).\n\n{ALTERNATIVES}"


def unsafe_request(question: str) -> Refusal | None:
    """G3: a request to pressure or penalize riders, or to skip their breaks."""
    if POLICY_QUESTION.match(question) or not re.search(
        RIDERS,
        question,
        re.IGNORECASE,
    ):
        return None
    kinds = [
        kind
        for kind, (pattern, _) in UNSAFE_REQUESTS.items()
        if pattern.search(question)
    ]
    if not kinds:
        return None
    reasons = [UNSAFE_REQUESTS[kind][1] for kind in kinds]
    return Refusal(
        _refusal(reasons, "DD-RIDER-001, Non-negotiable safety rules"),
        Finding("G3", f"Unsafe request: {', '.join(kinds)}"),
    )


def drafted_pressure(reply: str) -> Refusal | None:
    """G3: a reply that drafts pressure or a penalty instead of declining it."""
    for sentence in _sentences(reply):
        text = sentence.group()
        match = DRAFTED_PRESSURE.search(text)
        # The phrase itself may hold a negation ("no excuses"); check the rest.
        if match and not NEGATION.search(text.replace(match.group(), "")):
            return Refusal(
                _refusal(
                    [UNSAFE_REQUESTS["pressure"][1], UNSAFE_REQUESTS["penalty"][1]],
                    "DD-RIDER-001, Non-negotiable safety rules",
                ),
                Finding("G3", f"Reply drafted rider pressure: {match.group()!r}"),
            )
    return None


def _rider_limits(rider: dict[str, Any]) -> list[str]:
    reasons = []
    if rider["hours_on_shift"] >= MAX_SHIFT_HOURS:
        reasons.append(
            f"{rider['hours_on_shift']:g} h on shift; the maximum is "
            f"{MAX_SHIFT_HOURS} h including breaks",
        )
    if rider["minutes_since_last_break"] >= BREAK_DUE_MIN:
        reasons.append(
            f"{rider['minutes_since_last_break']} min since the last break; "
            "a 15-minute break is due",
        )
    return reasons


def _mentions(text: str, rider: dict[str, Any]) -> bool:
    names = {rider["rider_id"], rider["name"], rider["name"].split()[0]}
    return any(
        re.search(rf"\b{re.escape(name)}\b", text, re.IGNORECASE) for name in names
    )


def _endorses(text: str) -> bool:
    return bool(ASSIGN.search(text)) and not NEGATION.search(text)


def rider_hours_refusal(
    question: str,
    reply: str,
    live: dict[str, Any] | None,
) -> Refusal | None:
    """G4: the reply agrees to keep or extend a rider the question named."""
    if not KEEP_REQUEST.search(question):
        return None
    if live is None:
        if any(_endorses(item.group()) for item in _sentences(reply)):
            return Refusal(
                "I can't recommend keeping a rider on: their hours could not be "
                "verified from live rider-status data, and an unverified rider is "
                "not assigned or extended (DD-RIDER-001, Maximum shift and "
                f"mandatory break).\n\n{ALTERNATIVES}",
                Finding("G4", "Extension endorsed without live rider data"),
            )
        return None
    for rider in live["riders"]:
        if not _mentions(question, rider):
            continue
        if not any(
            _mentions(item.group(), rider) and _endorses(item.group())
            for item in _sentences(reply)
        ):
            continue
        hours = rider["hours_on_shift"]
        left = max(MAX_SHIFT_HOURS - hours, 0)
        text = (
            f"I can't recommend keeping {rider['name']} on. Verified as of "
            f"{live['as_of'][11:16]}: {hours:g} h on shift and "
            f"{rider['minutes_since_last_break']} min since the last break. The "
            f"maximum shift is {MAX_SHIFT_HOURS} hours including breaks, with no "
            "exceptions, and a rider must not be scheduled into a peak that would "
            "take them past it (DD-RIDER-001, Maximum shift and mandatory break)."
        )
        if left:
            text += (
                f" {rider['name']} has at most {left:g} h left, and there is no "
                "end time I can check against that."
            )
        return Refusal(
            f"{text}\n\n{ALTERNATIVES}",
            Finding("G4", f"Extension of {rider['name']} ({hours:g} h) endorsed"),
        )
    return None


def rider_hours_fix(reply: str, live: dict[str, Any] | None) -> Fixed | None:
    """G4: remove a recommendation to use a rider who is at a limit."""
    if live is None:
        return None
    text, findings = reply, []
    for rider in live["riders"]:
        reasons = _rider_limits(rider)
        if not reasons:
            continue
        for sentence in _sentences(text):
            part = sentence.group()
            if _mentions(part, rider) and _endorses(part):
                note = (
                    f"(Removed: {rider['name']} can't be assigned: "
                    f"{'; '.join(reasons)} (DD-RIDER-001).)"
                )
                text = text.replace(part, note, 1)
                findings.append(Finding("G4", f"{rider['name']}: {'; '.join(reasons)}"))
    return Fixed(text, findings) if findings else None


def _batch_problems(
    ids: list[str],
    orders: dict[str, dict[str, Any]],
) -> tuple[list[str], list[str]]:
    """Failed checks, and checks that cannot be completed, for one proposed batch."""
    failed, unknown = [], []
    rows = [orders[order_id] for order_id in ids if order_id in orders]
    if missing := [order_id for order_id in ids if order_id not in orders]:
        unknown.append(f"{', '.join(missing)} not in the live data")
    if len(ids) > MAX_BATCH_ORDERS:
        failed.append(
            f"more than {MAX_BATCH_ORDERS} orders, which needs a declared surge "
            "and your approval",
        )
    if frozen := [row["order_id"] for row in rows if row["has_frozen_items"]]:
        failed.append(f"{', '.join(frozen)} has frozen items (single-drop only)")
    if (items := sum(row["item_count"] for row in rows)) > MAX_BATCH_ITEMS:
        failed.append(f"{items} items in total, above {MAX_BATCH_ITEMS}")
    if rows and (age := max(row["age_sec"] for row in rows)) >= MAX_BATCH_AGE_SEC:
        failed.append(f"the older order has waited {age} s, at or above 480 s")
    if len({row["zone_id"] for row in rows}) > 1:
        unknown.append("the zones are not confirmed adjacent")
    return failed, unknown


def batching_fix(reply: str, live: dict[str, Any] | None) -> Fixed | None:
    """G7: a proposed batch that fails a check, or presents unknowns as eligible."""
    orders = {
        row["order_id"]: row for row in (live or {}).get("queue", {}).get("orders", [])
    }
    text, findings, notes = reply, [], []
    for sentence in _sentences(reply):
        part = sentence.group()
        ids = list(dict.fromkeys(ORDER_ID.findall(part)))
        if len(ids) < 2 or not BATCH.search(part) or NEGATION.search(part):
            continue
        pair = " + ".join(ids)
        failed, unknown = (
            _batch_problems(ids, orders) if live else ([], ["no live order data"])
        )
        if failed:
            text = text.replace(
                part,
                f"{pair} can't be batched: {'; '.join(failed)} (DD-BATCH-001).",
                1,
            )
            findings.append(Finding("G7", f"{pair}: {'; '.join(failed)}"))
        elif not CONDITIONAL.search(part):
            gaps = [*unknown, "the added detour is not in the live data"]
            notes.append(
                f"Guardrail note: {pair} is a candidate only; {', and '.join(gaps)}, "
                "so it is not confirmed eligible (DD-BATCH-001).",
            )
            findings.append(Finding("G7", f"{pair}: {'; '.join(gaps)}"))
    if not findings:
        return None
    return Fixed("\n\n".join([text, *notes]), findings)


def execution_claims_fix(reply: str) -> Fixed | None:
    """G5: rewrite a claim that an action was carried out as a proposal."""
    claims = [match.group() for match in CLAIM.finditer(reply)]
    if not claims:
        return None
    text = CLAIM.sub(
        lambda match: f"I propose to {BASE_VERB[match.group(1).lower()]}",
        reply,
    )
    return Fixed(
        f"{text}\n\n{EXECUTION_NOTE}",
        [Finding("G5", f"Claimed execution: {claim!r}") for claim in claims],
    )


# Figures with an operational unit; order IDs, times and money are not matched.
FIGURE = re.compile(
    r"(?<![\w.:₹-])(\d+(?:\.\d+)?)\s*(%|percent\b|orders?\b|riders?\b|minutes?\b"
    r"|mins?\b|items?\b|km\b|deliveries\b|hours?\b|seconds?\b|secs?\b)",
    re.IGNORECASE,
)
NUMBER = re.compile(r"\d+(?:\.\d+)?")
# Thresholds the playbook states, which a reply may quote without a tool call.
POLICY_FIGURES = {0.8, 1, 2, 3, 4, 8, 9, 10, 15, 20, 30, 40, 240, 480}
FRESHNESS = re.compile(
    r"\bstale\b|out of date|outdated|not current|may have changed|old snapshot",
    re.IGNORECASE,
)
UNREACHABLE = re.compile(
    r"could(?: not|n't) (?:be )?reach|can(?:no|')t reach|unavailable|not available"
    r"|could(?: not|n't) (?:be )?(?:read|load|fetch)",
    re.IGNORECASE,
)
PROMISE = re.compile(
    r"\b(?:guarantee\w*|promise\w*|definitely|for sure)\b",
    re.IGNORECASE,
)
ARRIVAL = re.compile(
    r"\b(?:will|'ll|should)\s+(?:arrive|be delivered|reach|be there|get there)\b",
    re.IGNORECASE,
)
DELIVERY_TIME = re.compile(r"\b\d+\s*min(?:ute)?s?\b|\b\d{1,2}:\d{2}\b", re.IGNORECASE)
ESTIMATE = re.compile(
    r"\bestimat\w*|\bapprox\w*|\babout\b|\baround\b|\broughly\b"
    # A range such as "6-9 min", not an order ID such as ORD-03-001.
    r"|(?<![\w-])\d+\s*(?:-|–|to)\s*\d+\b(?!-)",
    re.IGNORECASE,
)
INCENTIVE = re.compile(r"\b(?:incentive|bonus|surge pay)\w*", re.IGNORECASE)
RUPEES = re.compile(r"(?:₹|\brs\.?\s?|\binr\s?)(\d+(?:,\d{3})*)", re.IGNORECASE)
SURGE = re.compile(r"\bsurge\b", re.IGNORECASE)
ETA_NOTE = (
    "Guardrail note: any delivery time above is an estimate from current data, "
    "not a promise (DD-COMMS-001)."
)


def _numbers(value: Any, key: str = "") -> set[float]:
    """Every number in a tool result, with seconds also as minutes."""
    if isinstance(value, dict):
        return {n for k, v in value.items() for n in _numbers(v, k)}
    if isinstance(value, list):
        return {n for item in value for n in _numbers(item, key)}
    if isinstance(value, bool) or not isinstance(value, int | float):
        return set()
    found = {float(value), float(round(value)), round(value, 1)}
    if key.endswith("_sec"):
        minutes = value / 60
        found |= {round(minutes, 1), float(round(minutes)), float(int(minutes))}
    return found


def unverified_figures(
    reply: str,
    results: list[ToolResult],
    references: str,
) -> Finding | None:
    """G1: operational figures that appear in no tool result or reference.

    References are the alerts, settings and earlier messages the model saw.
    Flag only: a figure the model derived itself is also reported.
    """
    allowed = POLICY_FIGURES | {float(n) for n in NUMBER.findall(references)}
    for item in results:
        allowed |= _numbers(item["result"])
    missing = [
        match.group()
        for match in FIGURE.finditer(reply)
        if float(match.group(1)) not in allowed
        and float(round(float(match.group(1)))) not in allowed
    ]
    if not missing:
        return None
    return Finding("G1", f"Not found in tool data: {', '.join(dict.fromkeys(missing))}")


def stale_data_fix(reply: str, results: list[ToolResult]) -> Fixed | None:
    """G8: live figures quoted from a stale snapshot, or with no snapshot at all."""
    if not any(item["tool"] == "get_live_dispatch_status" for item in results):
        return None
    live = live_status(results)
    if live is not None:
        if not live.get("stale") or not FIGURE.search(reply) or FRESHNESS.search(reply):
            return None
        time = live["as_of"][11:16]
        return Fixed(
            f"{reply}\n\nGuardrail note: the live data is from {time} IST and may "
            "be out of date (DD-SOP-001).",
            [Finding("G8", f"Stale snapshot from {time} quoted as current")],
        )
    counts = [
        match.group()
        for match in FIGURE.finditer(reply)
        if match.group(2).lower().startswith(("order", "rider"))
    ]
    if not counts or UNREACHABLE.search(reply):
        return None
    return Fixed(
        f"{reply}\n\nGuardrail note: live dispatch data could not be reached, so "
        "these figures are not current (DD-SOP-001).",
        [Finding("G8", f"Live figures without live data: {', '.join(counts)}")],
    )


def eta_promise_fix(reply: str) -> Fixed | None:
    """G2: a delivery time given as a promise or as a single exact time."""
    promised = [
        sentence.group().strip()
        for sentence in _sentences(reply)
        if DELIVERY_TIME.search(sentence.group())
        and not NEGATION.search(sentence.group())
        and (
            PROMISE.search(sentence.group())
            or (
                ARRIVAL.search(sentence.group())
                and not ESTIMATE.search(sentence.group())
            )
        )
    ]
    if not promised:
        return None
    return Fixed(
        f"{reply}\n\n{ETA_NOTE}",
        [Finding("G2", f"Time given as a promise: {text!r}") for text in promised],
    )


def _proposes_batch(text: str) -> bool:
    return (
        len(set(ORDER_ID.findall(text))) >= 2
        and bool(BATCH.search(text))
        and not NEGATION.search(text)
    )


def preference_conflict_fix(
    question: str,
    reply: str,
    live: dict[str, Any] | None,
    settings: dict[str, Any],
) -> Fixed | None:
    """G6: a proposal that a stored setting rules out, without saying so."""
    notes, findings = [], []
    if settings.get("surge_only_batching") and not SURGE.search(question + reply):
        ratio = (live or {}).get("summary", {}).get("pending_per_available_rider")
        threshold = settings.get("rider_shortage_threshold")
        # With the shortage alert off there is no limit to compare against.
        short = ratio is None or threshold is None or ratio > threshold
        if not short and any(_proposes_batch(s.group()) for s in _sentences(reply)):
            notes.append(
                'Guardrail note: your setting "Batch only when short of riders" '
                f"is on, and there are {ratio:g} pending orders per available rider, "
                f"within your limit of {threshold:g}. Batch only if you declare a "
                "surge (DD-BATCH-001, Preference interaction).",
            )
            findings.append(Finding("G6", "Batch proposed while not short of riders"))
    cap = settings.get("incentive_cap")
    for sentence in _sentences(reply):
        text = sentence.group()
        if not INCENTIVE.search(text) or NEGATION.search(text):
            continue
        for amount in (int(a.replace(",", "")) for a in RUPEES.findall(text)):
            if cap is None:
                notes.append(
                    "Guardrail note: no incentive cap is set in your settings, so "
                    f"no amount should be proposed; ₹{amount} needs a cap or "
                    "regional ops sign-off (DD-WEATHER-001).",
                )
                findings.append(Finding("G6", f"₹{amount} incentive with no cap set"))
            elif amount > cap:
                notes.append(
                    f"Guardrail note: ₹{amount} is above your incentive cap of "
                    f"₹{cap:g} per shift; anything above it needs regional ops "
                    "sign-off (DD-WEATHER-001).",
                )
                findings.append(Finding("G6", f"₹{amount} above the ₹{cap:g} cap"))
    if not findings:
        return None
    return Fixed("\n\n".join([reply, *dict.fromkeys(notes)]), findings)
