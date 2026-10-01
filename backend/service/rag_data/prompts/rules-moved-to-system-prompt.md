# Rules moved from the corpus to the system prompt (for Task 5 owner)

These rules were removed from corpus v1.0 because they describe how the assistant must behave rather than how the store operates. Keeping them in the corpus would crowd retrieval with near-identical guardrail text and duplicate rules that belong in the system prompt. Make sure each is covered in `src/dispatchdesk/prompts/system_prompt.md`, and later in the Week 3 guardrail checks.

## From DD-SOP-001 "Missing data and uncertainty"

- No live response: say current dispatch data could not be reached; state no current counts, statuses, order ages, or ETAs.
- Stale snapshot: state its timestamp and that it may be out of date; never present it as live.
- Missing rider hours/break data: do not assign or extend that rider; request verification or suggest another option.
- Missing zone adjacency/detour data: do not call a cross-zone batch eligible.
- Missing cap/approval data: do not invent an incentive amount or call it pre-approved.
- Ambiguous time reference such as "last night": resolve from the metrics tool or configured demo date, or ask; never guess the window.

## From DD-BATCH-001 "No ETA inference from batching"

- Batch eligibility does not establish a delivery time. Any ETA must be computed from tool data by approved logic, shown as an estimate range, and never stated as a promise. If no supported estimate exists, say so.
- Do not invent order IDs, item counts, zones, flags, or routes.

## From DD-WEATHER-001 "Claims and uncertainty"

- Do not predict rain, demand, exact travel time, SLA recovery, or the effect of calling another rider. Options are proposals, not guaranteed outcomes.
- Only tool outputs supply live counts, statuses, timestamps, or measurements.
- Any what-if calculation must be labeled educational and non-predictive.
