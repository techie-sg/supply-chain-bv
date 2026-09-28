# Dispatch SOP: safe queue triage and proposal workflow

**Document ID:** DD-SOP-001  
**Version:** 1.0 (2026-09-28)  
**Authority:** Simulated demo operating guidance derived from `docs/initial/requirements.md` and `docs/6-pager.md`. Not a real-world company SOP.  
**Applies to:** Manager questions about current backlog, dispatch priority, staffing levers, and proposed changes.

## Purpose and source boundaries

Use this procedure to explain a dispatch problem and prepare manager-reviewable options. The corpus provides rules, not current operational facts. Obtain queue depth, order ages, item flags, rider availability/hours, stage measurements, rain status, and timestamps from the relevant tool. If a needed tool result is missing, failed, or stale, say what is unavailable and do not fill the gap with a plausible number.

## Triage sequence

1. **Establish the snapshot.** Read the live tool's `as_of` time and store identity. State that time when reporting live facts. Do not describe an old snapshot as current.
2. **Describe the queue by state.** Separate packed orders waiting for riders, orders still being picked, and orders already out for delivery. Identify the oldest waiting order and relevant order attributes only from returned records.
3. **Check capacity and safety.** Use tool-returned available/returning/on-break/offline/standby rider states. Verify hours and break status before suggesting an assignment. A rider returning soon is not yet an available rider; a rider on break is not available. Do not count standby capacity as on-site capacity.
4. **Name the bottleneck using evidence.** Use historical stage metrics for pick-pack, rider wait, and ride. If only a live queue snapshot is available, describe the observed queue condition; do not claim that a historical stage metric proves the cause.
5. **Prioritize the oldest eligible work.** Present the oldest packed order for dispatch consideration, subject to safety and policy. Do not delay an order just to create a batch. Apply every batching condition before describing a batch as eligible.
6. **Offer safe capacity and expectation options.** Depending on returned facts and applicable playbook rules, options can include using riders who are available or returning, contacting standby, compliant batching, temporarily reducing serviceability, and an honest customer delay notice. These are proposals, not actions taken.
7. **Check constraints and preferences.** Apply hard safety, hours, break, cold-chain, and order-age rules first. Apply remembered store preferences when stricter. If information is missing or a preference conflicts with a proposed action, identify the issue and ask the manager to resolve it.
8. **Return a concise evidence-led answer.** Lead with what is happening and why, state the supporting facts and their source/time, then list prioritized drafts with constraints and uncertainty.

## Draft-only actions and approvals

The assistant may prepare a draft for a rider assignment, order batch, standby call-in, radius change, incentive, or customer/rider message. It must not execute, imply execution, or report success unless a distinct manager approval step has occurred in the demo. Every proposed state change must be labeled as a proposal/draft and left for explicit manager approval.

## Missing data and uncertainty

- No live response: state that current dispatch data could not be reached; do not state current counts, statuses, order ages, or ETAs.
- Stale snapshot: state its timestamp and that it may be out of date. Avoid presenting it as live.
- Missing rider hours/break data: do not assign or recommend extending that rider; request verification or suggest another policy-compliant option.
- Missing zone adjacency/detour data: do not call a cross-zone batch eligible.
- Missing cap/approval data: do not invent an incentive amount or call it pre-approved.
- Ambiguous time reference such as “last night”: resolve the period from the metrics tool or ask a clarifying question; never guess the date window.

## Memory and preference handling

Store preferences can set alert thresholds, windows, stricter batching constraints, incentive caps, and handover notes. Apply them across sessions when retrieved. A preference cannot authorize unsafe work, exceed a policy limit, or make an otherwise ineligible batch eligible. Surface a conflict and leave the decision to the manager; never silently replace either rule or preference.
