"""Policy checks on a question and its reply, against the answer's tool results.

Pure functions with no model call; `service.guardrails` runs them as NeMo
Guardrails actions. Rule IDs (G1 to G8) are those in docs/guardrails.md.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Any, TypeGuard

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
POLICY_INFORMATION = re.compile(
    r"^\s*(?:please\s+)?(?:explain|describe|summari[sz]e|outline|tell\s+me)\s+"
    r"(?:(?:our|the|store|rider|current|company)\s+)*(?:polic(?:y|ies)|rules?|SOPs?)\b",
    re.IGNORECASE,
)
SAFETY_EXPLANATION = re.compile(
    r"^\s*(?:please\s+)?explain\s+why\b[^.!?\n]*"
    r"\b(?:should|must)\s+(?:not|never)\b",
    re.IGNORECASE,
)
FOLLOW_ON_REQUEST = re.compile(
    r"(?:[,;:.!\n]|\b(?:and|then|also|next|but|before|after|while)\b)\s*"
    r"(?:(?:please|then)\s+)?(?:(?:can|could|would|will)\s+you\s+)?"
    r"(?:draft\w*|compos\w*|writ(?:e|ing)|send\w*|tell\w*|ask\w*|"
    r"instruct\w*|make|have|order|encourag\w*|pressure|help)\b"
    r"|\b(?:include|including|with)\s+(?:(?:a|an|the)\s+)?"
    r"(?:draft|message|instructions)\b",
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
ASSIGNMENT_CLAUSE_SEPARATOR = re.compile(
    r";|\bbut\b|(?:,|\band\b)\s*(?="
    r"(?:(?:I|we|you)\s+)?"
    r"(?:(?:do\s+not|don't|never|cannot|can|could|should|would|will|must)\s+)*"
    r"(?:assign|reassign|dispatch|send|use|allocate|schedule|extend|keep|stay)\b)",
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
RIDER_ID = re.compile(r"\bRDR-[A-Z0-9-]+\b", re.IGNORECASE)
RIDER_TARGET = re.compile(
    r"\b(?:(?:re)?assign(?:ed|ing|s)?|allocat(?:e|es|ed|ing)|"
    r"schedul(?:e|es|ed|ing)|extend(?:ed|ing|s)?)\s+"
    r"(?:(?:the\s+)?(?:rider|driver)\s+)?"
    r"(?:\*\*|`)?(?P<target>[\w'-]+)",
    re.IGNORECASE,
)
NAMED_SEND_TARGET = re.compile(
    r"\b(?i:send|sent|sending|sends|use|used|using|uses|dispatch(?:ed|ing|es)?)\s+"
    r"(?:\*\*|`)?(?P<target>(?i:him|her|them)\b|[A-Z][\w'-]*)",
)
GENERIC_ASSIGNMENT_TARGETS = {
    # An order or an adverb after the verb ("assign it", "assigned shortly") is
    # not a rider. A list, not an "-ly" test, so a rider named Kelly still counts.
    "it",
    "this",
    "that",
    "these",
    "those",
    "one",
    "ones",
    "shortly",
    "soon",
    "promptly",
    "immediately",
    "quickly",
    "next",
    "first",
    "now",
    "today",
    "later",
    "again",
    "a",
    "an",
    "the",
    "any",
    "all",
    "each",
    "every",
    "only",
    "available",
    "eligible",
    "rider",
    "riders",
    "driver",
    "drivers",
    "order",
    "orders",
    "deliveries",
    "priority",
    "responsibility",
    "tasks",
    "capacity",
    "breaks",
    "pickups",
    "policy",
    "playbook",
    "live",
    "verified",
    "current",
    "single-drop",
    "operations",
    "updates",
    "messages",
    "emails",
    "notes",
}
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
    informational = bool(
        (POLICY_INFORMATION.match(question) or SAFETY_EXPLANATION.match(question))
        and not FOLLOW_ON_REQUEST.search(question),
    )
    if (
        POLICY_QUESTION.match(question)
        or informational
        or not re.search(
            RIDERS,
            question,
            re.IGNORECASE,
        )
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
    hours = rider.get("hours_on_shift")
    since_break = rider.get("minutes_since_last_break")
    if not _verified_number(hours):
        reasons.append("hours on shift could not be verified")
    elif hours >= MAX_SHIFT_HOURS:
        reasons.append(
            f"{hours:g} h on shift; the maximum is "
            f"{MAX_SHIFT_HOURS} h including breaks",
        )
    if not _verified_number(since_break):
        reasons.append("time since the last break could not be verified")
    elif since_break >= BREAK_DUE_MIN:
        reasons.append(
            f"{since_break:g} min since the last break; a 15-minute break is due",
        )
    if not isinstance(rider.get("status"), str) or not rider["status"].strip():
        reasons.append("rider status could not be verified")
    if not isinstance(rider.get("rider_id"), str) or not rider["rider_id"].strip():
        reasons.append("rider identity could not be verified")
    return reasons


def _verified_number(value: Any) -> TypeGuard[int | float]:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and value >= 0
        and (not isinstance(value, float) or isfinite(value))
    )


def _rider_snapshot_problem(live: dict[str, Any] | None) -> str | None:
    if live is None:
        return "live rider-status data could not be reached"
    if live.get("stale") is not False:
        return (
            "the rider-status snapshot is stale"
            if live.get("stale") is True
            else "the rider-status snapshot freshness could not be verified"
        )
    try:
        as_of = datetime.fromisoformat(live["as_of"])
        if as_of.utcoffset() is None:
            return "the rider-status snapshot time could not be verified"
    except (KeyError, TypeError, ValueError):
        return "the rider-status snapshot time could not be verified"
    if not isinstance(live.get("riders"), list) or any(
        not isinstance(rider, dict) for rider in live["riders"]
    ):
        return "the rider-status records could not be verified"
    return None


def _mentions(text: str, rider: dict[str, Any]) -> bool:
    names = {
        value
        for key in ("rider_id", "name")
        if isinstance(value := rider.get(key), str) and value.strip()
    }
    if isinstance(name := rider.get("name"), str) and name.split():
        names.add(name.split()[0])
    return any(
        re.search(rf"\b{re.escape(name)}\b", text, re.IGNORECASE) for name in names
    )


def _endorses(text: str) -> bool:
    action = ASSIGN.search(text)
    # "Assign Suresh, don't delay" still endorses the assignment. Only a
    # negation before its action can decline that action.
    return action is not None and not NEGATION.search(text[: action.start()])


def _assignment_clauses(text: str) -> list[str]:
    """Keep a declined rider separate from a later positive recommendation."""
    return [
        part.strip()
        for sentence in _sentences(text)
        for part in ASSIGNMENT_CLAUSE_SEPARATOR.split(sentence.group())
        if part.strip()
    ]


def _concrete_rider_target(text: str) -> bool:
    """Recognize a proposed person even when the tool returned no rider rows."""
    target = RIDER_TARGET.search(text) or NAMED_SEND_TARGET.search(text)
    return bool(
        target
        and target["target"].casefold() not in GENERIC_ASSIGNMENT_TARGETS
        and not ORDER_ID.fullmatch(target["target"]),
    )


def rider_hours_refusal(
    question: str,
    reply: str,
    live: dict[str, Any] | None,
) -> Refusal | None:
    """G4: the reply agrees to keep or extend a rider the question named."""
    if not KEEP_REQUEST.search(question):
        return None
    clauses = _assignment_clauses(reply)
    if problem := _rider_snapshot_problem(live):
        if any(_endorses(part) for part in clauses):
            return Refusal(
                "I can't recommend keeping a rider on: their hours could not be "
                f"verified because {problem}, and an unverified rider is "
                "not assigned or extended (DD-RIDER-001, Maximum shift and "
                f"mandatory break).\n\n{ALTERNATIVES}",
                Finding("G4", "Extension endorsed without live rider data"),
            )
        return None
    for rider in (live or {}).get("riders", []):
        if not _mentions(question, rider):
            continue
        if not any(_mentions(part, rider) and _endorses(part) for part in clauses):
            continue
        if not _verified_number(rider.get("hours_on_shift")) or not _verified_number(
            rider.get("minutes_since_last_break"),
        ):
            return Refusal(
                "I can't recommend keeping this rider on: their hours and break "
                f"status could not be verified (DD-RIDER-001).\n\n{ALTERNATIVES}",
                Finding(
                    "G4",
                    "Extension endorsed without verified rider hours or break status",
                ),
            )
        hours = rider["hours_on_shift"]
        name = rider.get("name") or rider.get("rider_id") or "this rider"
        left = max(MAX_SHIFT_HOURS - hours, 0)
        text = (
            f"I can't recommend keeping {name} on. Verified as of "
            f"{(live or {})['as_of'][11:16]}: {hours:g} h on shift and "
            f"{rider['minutes_since_last_break']} min since the last break. The "
            f"maximum shift is {MAX_SHIFT_HOURS} hours including breaks, with no "
            "exceptions, and a rider must not be scheduled into a peak that would "
            "take them past it (DD-RIDER-001, Maximum shift and mandatory break)."
        )
        if left:
            text += (
                f" {name} has at most {left:g} h left, and there is no "
                "end time I can check against that."
            )
        return Refusal(
            f"{text}\n\n{ALTERNATIVES}",
            Finding("G4", f"Extension of {name} ({hours:g} h) endorsed"),
        )
    return None


def rider_hours_fix(reply: str, live: dict[str, Any] | None) -> Fixed | None:
    """G4: require fresh verified rider facts before a specific assignment."""
    text, findings = reply, []
    rows = (live or {}).get("riders", [])
    riders = (
        [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    )
    for part in _assignment_clauses(reply):
        if not _endorses(part):
            continue
        named = [rider for rider in riders if _mentions(part, rider)]
        exact = [
            rider
            for rider in named
            if any(
                isinstance(value := rider.get(key), str)
                and value.strip()
                and re.search(rf"\b{re.escape(value)}\b", part, re.IGNORECASE)
                for key in ("rider_id", "name")
            )
        ]
        if exact:
            named = exact
        # An order with no rider ("Dispatch ORD-02-001 first") proposes no one to
        # verify; G5 still handles claims that it was done.
        specific = bool(named or RIDER_ID.search(part) or _concrete_rider_target(part))
        if not specific:
            continue
        # A rider/customer message is not an assignment. Keep ordinary drafts
        # unchanged; operational claims still pass through the execution rail.
        communication = re.search(
            r"\bsend\w*\b.*?\b(message|update|email|note)\b",
            part,
            re.IGNORECASE,
        )
        order = ORDER_ID.search(part)
        if (
            communication
            and (order is None or communication.end() < order.start())
            and not re.search(
                r"\b(?:assign|reassign|dispatch|allocate|schedule|extend)\w*\b",
                part,
                re.IGNORECASE,
            )
        ):
            continue
        problem = _rider_snapshot_problem(live)
        ids = {identifier.upper() for identifier in RIDER_ID.findall(part)}
        known_ids = {str(rider.get("rider_id", "")).upper() for rider in named}
        if problem is None and (not named or ids - known_ids):
            problem = (
                "the proposed rider could not be identified in live rider-status data"
            )
        if problem is None and len(named) > 1 and not exact:
            problem = "the rider name is ambiguous in live rider-status data"
        if problem:
            text = text.replace(
                part,
                f"(Removed: this rider assignment cannot be recommended because {problem}. Verify fresh rider hours and break status first (DD-RIDER-001).)",
                1,
            )
            findings.append(
                Finding("G4", f"Assignment without verified rider facts: {problem}"),
            )
            continue
        for rider in named:
            if reasons := _rider_limits(rider):
                name = rider.get("name") or rider.get("rider_id") or "This rider"
                text = text.replace(
                    part,
                    f"(Removed: {name} can't be assigned: {'; '.join(reasons)} (DD-RIDER-001).)",
                    1,
                )
                findings.append(Finding("G4", f"{name}: {'; '.join(reasons)}"))
                break
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
    if isinstance(value, float) and not isfinite(value):
        # Invalid tool figures must not abort the rails before rider facts can
        # be rejected by G4.
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
