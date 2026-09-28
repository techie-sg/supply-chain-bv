# Order batching and cold-chain rules

**Document ID:** DD-BATCH-001  
**Version:** 1.0 (2026-09-28)  
**Authority:** Simulated demo rules explicitly listed in `docs/6-pager.md` Appendix D and `docs/initial/requirements.md`.  
**Applies to:** Any suggestion to place more than one order on a rider's trip.

## Eligibility is conjunctive

A batch is eligible only when every applicable check below passes using the order and routing attributes returned by tools. A manager approval or incentive does not waive cold-chain, geography, detour, order-age, or safety rules.

1. **Order count:** At most 2 orders per rider. A third order may be considered only in a declared surge with explicit manager approval, and only if every other policy and safety check also passes. It is not the default.
2. **Geography:** Orders must be in the same zone or in zones explicitly identified as adjacent by the configured zone map.
3. **Added detour:** The added detour must be no more than about 0.8 km **or** 2 minutes, according to the playbook wording. Because the source does not specify whether both caps must pass or one is sufficient, use the stricter interpretation: require both distance and time values when available and reject the batch if either exceeds its cap. If either value is unavailable, do not assert eligibility.
4. **Combined items:** The orders must contain 15 or fewer items in total.
5. **Cold chain:** Frozen or ice-cream orders are single-drop only. Do not batch them with any other order, even if the other checks pass.
6. **Order age:** Do not batch when the older order has passed the 8-minute queue limit. The source does not resolve equality at exactly 8:00 (480 seconds). Use a conservative demo default: do not newly batch when the older order is at or beyond 480 seconds. The team can revise this only after recording an explicit policy decision. This age rule never delays an order that is already waiting; prioritize dispatch and consider a delay notice instead.

## Required inputs before recommending a specific pair

Obtain for each order: unique ID, status, zone, item count, frozen/ice-cream flag, and queue age. Obtain the configured zone adjacency and route detour estimates, and current rider capacity/state. If any eligibility input is missing, say which check could not be completed and do not label the pair “eligible.” Do not invent IDs, item counts, zones, flags, or routes.

## How to present a batch proposal

For each proposed pair, state the order IDs, the checks passed (same/adjacent zone, measured detour, combined item count, age, cold-chain status, order count), any relevant uncertainty, and that it is a draft requiring manager approval. Do not describe a proposal as assigned or dispatched. List excluded orders separately with the specific failed or unknown check—for example, frozen item, excessive age, too many items, zones not confirmed adjacent, or missing detour data.

## Preference interaction

A manager's stored rule such as “never batch frozen items” is consistent with this policy and should be applied. Stricter preferences (for example, a lower item limit or same-zone-only rule) narrow eligibility. A preference cannot relax any limit in this document. If the manager preference and system policy differ, surface the conflict; do not silently choose the more permissive rule.

## No ETA inference from batching

Batch eligibility does not establish a delivery time. Any ETA shown must be computed from tool-returned data by the approved application logic, shown as an estimate range, and never stated as a promise. If no supported estimate is available, say so rather than fabricating one.
