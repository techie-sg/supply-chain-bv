# Order batching and cold-chain rules

**Document ID:** DD-BATCH-001  
**Version:** 1.1 (2026-09-29)  
**Authority:** Simulated demo rules. Primary source: *Dark Store Last-Mile Dispatch Playbook* (sample operations document, illustrative thresholds only), section 3. Interpretations of unresolved boundaries are team decisions recorded in the corpus README.  
**Applies to:** Any suggestion to place more than one order on a rider's trip.

## Why batching is a surge tool, not a default

Batching puts two orders on one rider to relieve rider shortage. It is a tool for surges, not a default, because each extra order adds wait for the first customer. Always dispatch the oldest waiting order first, and never hold an order back to wait for a batch partner. Batch eligibility does not establish a delivery time for either order.

## Eligibility is conjunctive

A batch is eligible only when every applicable check below passes using the order and routing attributes returned by tools. A manager approval or incentive does not waive cold-chain, geography, detour, order-age, or safety rules.

1. **Order count:** At most 2 orders per rider. A third order may be considered only in a declared surge with explicit manager approval, and only if every other policy and safety check also passes. It is not the default.
2. **Geography:** Orders must be in the same zone or in zones explicitly identified as adjacent by the configured zone map.
3. **Added detour:** The playbook sets the added detour at no more than about 0.8 km or 2 minutes. Team decision: apply the stricter interpretation and require both distance and time to be within their caps; reject the batch if either exceeds its cap. If either value is unavailable, eligibility cannot be confirmed.
4. **Combined items:** The orders must contain 15 or fewer items in total, so they fit one rider bag.
5. **Cold chain:** Frozen or ice-cream orders are cold-chain items and are single-drop only. Do not batch them with any other order, even if the other checks pass. Dispatch them promptly.
6. **Order age:** Do not batch when the older order has passed 8 minutes in the queue, since a batch would push it further. Team decision: do not newly batch when the older order is at or beyond 480 seconds. This age rule never delays an order that is already waiting; prioritize its dispatch and consider a delay notice instead.

## Required inputs before recommending a specific pair

Each order needs: unique ID, status, zone, item count, frozen/ice-cream flag, and queue age. The configured zone adjacency, route detour estimates, and current rider capacity/state are also required. If any eligibility input is missing, the check cannot be completed and the pair is not eligible for a positive recommendation.

## How to present a batch proposal

For each proposed pair, state the order IDs, the checks passed (same/adjacent zone, measured detour, combined item count, age, cold-chain status, order count), any relevant uncertainty, and that it is a draft requiring manager approval. A proposal is not described as assigned or dispatched. List excluded orders separately with the specific failed or unknown check, for example: frozen item, excessive age, too many items, zones not confirmed adjacent, or missing detour data.

## Preference interaction

A manager's stored rule such as "never batch frozen items" is consistent with this policy and is applied. Stricter preferences (for example, a lower item limit or same-zone-only rule) narrow eligibility. A preference cannot relax any limit in this document. If the manager preference and system policy differ, surface the conflict; do not silently choose the more permissive rule.
