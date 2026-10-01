# Rain and surge playbook: protect capacity without adding rider risk

**Document ID:** DD-WEATHER-001  
**Version:** 1.1 (2026-09-29)  
**Authority:** Simulated demo guidance. Primary source: *Dark Store Last-Mile Dispatch Playbook* (sample operations document, illustrative thresholds only), sections 4 and 5.  
**Applies to:** Rain-affected operations, evening peaks, and declared surge conditions.

## What to expect in heavy rain

In heavy rain, expect riders to drop offline, ride times to rise by about 30 to 40 percent, and order volume to jump. Together these push up rider wait and ride time at the same moment. A missed SLA in heavy rain is not a rider performance problem and must not be treated as one. In heavy rain or flooding, a rider may decline or pause work without penalty.

## Diagnose before choosing a lever

Rain and peak demand can arrive together. Compare actual orders, available riders, pick-pack, rider wait, ride, and rain flag for the requested period. A common pattern is longer rider wait when demand rises and rider availability falls, alongside longer ride times in wet conditions. Treat it as a pattern to verify, not an assumption. Use DD-DIAG-001 to identify the measured bottleneck.

## Recommended levers, in order

First confirm the queue, rider states, order ages, and data freshness from the live tool, and dispatch the oldest eligible packed order first under DD-SOP-001. Then apply the playbook's levers in this order:

1. **Call in standby riders.** Standby riders are typically about 20 minutes away, so call early. Confirm availability and arrival information from the tool or configuration.
2. **Apply policy-compliant batching** for eligible orders only, under every DD-BATCH-001 condition. Never batch frozen or ice-cream items.
3. **Activate the pre-approved surge incentive**, capped at the store's approved per-order amount. Anything above the cap needs regional ops sign-off. Use an incentive only when the cap and approval are actually recorded in store configuration or memory; never invent an amount, and never frame an incentive as payment for faster riding.
4. **Temporarily shrink the serviceability radius** from 3 km to 2 km, or pause the outermost zone, to avoid accepting orders that cannot be served safely.
5. **Show customers an honest, longer ETA** instead of holding the 10-minute promise, following DD-COMMS-001.

If local compliant capacity is still insufficient, request extra riders from regional operations. Every lever is a draft proposal for manager approval; none is executed automatically.

## Declared surge and batch exception

The playbook allows consideration of a third order only with manager approval during a declared surge. This exception changes only the order-count limit; all zone, detour, item-count, cold-chain, age, rider-hours, and safety constraints remain in force. Rain alone does not automatically declare a surge. The manager must explicitly declare it, and the batch remains a draft for approval.

## Prohibited responses to rain or SLA pressure

Never advise riders to speed, jump signals, take unsafe routes, or ride through dangerous conditions to meet a delivery promise. Never recommend docking pay or otherwise penalizing a rider for a weather- or safety-related delay. Never disguise pressure as "motivation" or imply consent waives safety and hours rules. Follow the refusal guidance in DD-RIDER-001 and offer the compliant levers above.
