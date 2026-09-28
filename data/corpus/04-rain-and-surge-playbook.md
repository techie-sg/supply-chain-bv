# Rain and surge playbook: protect capacity without adding rider risk

**Document ID:** DD-WEATHER-001  
**Version:** 1.0 (2026-09-28)  
**Authority:** Simulated demo guidance derived from `docs/initial/requirements.md` and `docs/6-pager.md`.  
**Applies to:** Rain-affected operations, evening peaks, and declared surge conditions.

## Diagnose before choosing a lever

Rain and peak demand can arrive together. Compare actual orders, available riders, pick-pack, rider-wait, ride, and rain flag for the requested period. A common pattern is longer rider wait when demand rises and rider availability falls, alongside longer ride times in wet conditions. Treat it as a pattern to verify, not an assumption. Use DD-DIAG-001 to identify the measured bottleneck.

## Safe operating sequence

1. Confirm queue, rider states, order ages, and data freshness from the live tool.
2. Check the oldest eligible packed orders and dispatch priority under DD-SOP-001.
3. Use batching only when every DD-BATCH-001 condition passes; never batch frozen or ice-cream items.
4. Consider a standby call-in when the tool/configuration confirms availability and arrival information. State the source and label the call-in as a draft.
5. Consider temporary serviceability-radius reduction to avoid accepting work that cannot be served safely. Treat it as a manager-approved proposal; no radius change is executed by the assistant.
6. Use a pre-approved surge incentive only when the cap and approval are actually available from store configuration or memory. Never invent an amount, exceed the cap, or imply that an incentive purchases unsafe speed.
7. Set customer expectations with a truthful delay notice or supported ETA estimate from DD-COMMS-001.
8. Request regional operations support when local compliant capacity is insufficient.

## Declared surge and batch exception

The playbook allows consideration of a third order only with manager approval during a declared surge. This exception changes only the order-count limit; all zone, detour, item-count, cold-chain, age, rider-hours, and safety constraints remain in force. Do not infer that rain alone automatically declares a surge. The manager must explicitly declare it, and the batch must remain a draft for approval.

## Prohibited responses to rain or SLA pressure

Never advise riders to speed, jump signals, take unsafe routes, or ride through dangerous conditions to meet a delivery promise. Never recommend docking pay or otherwise penalizing a rider for a weather- or safety-related delay. Never disguise pressure as “motivation” or imply consent waives safety and hours rules. Escalate the issue through refusal guidance in DD-RIDER-001 and offer compliant capacity/expectation options above.

## Claims and uncertainty

This playbook does not predict rain, demand, exact travel time, SLA recovery, or the effect of calling an additional rider. Describe options as operational proposals, not guaranteed outcomes. Only tool outputs may supply live counts, statuses, timestamps, or measurements. A what-if calculation, if later added, must be clearly labeled educational and non-predictive.
