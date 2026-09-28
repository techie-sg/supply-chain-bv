# Delay diagnosis guide: identify which stage is slipping

**Document ID:** DD-DIAG-001  
**Version:** 1.0 (2026-09-28)  
**Authority:** Simulated demo guidance from `docs/initial/requirements.md` and `docs/6-pager.md`, including its Dispatch Playbook appendix.  
**Applies to:** Live backlog explanations and historical delivery-stage comparisons.

## Delivery stages and diagnostic reference points

A delivery's elapsed time is composed of three stages:

- **Pick-pack:** order placed until the order is packed and ready. Reference target: about 3 minutes.
- **Rider wait:** packed order waits until a rider picks it up. Reference target: about 1.5 minutes.
- **Ride:** rider pickup until delivery. Reference target: about 5 minutes within a 2 km radius.

These are approximate diagnostic reference points, not guarantees, hard service limits, or predictions for an individual order. A stage above its reference deserves investigation; it does not, by itself, establish the cause.

## Diagnosis from historical metrics

For a comparison, retrieve matching hourly periods for both dates from the delivery-metrics tool. Compare orders, rider count, rain flag, pick-pack, rider wait, ride, average delivery, and SLA where those fields are returned. Calculate stage differences from the returned values, keeping units consistent. Identify the stage with the largest contribution to the increase in average delivery time; describe other contributing stages too. State the data period and actual figures. Do not infer causation from correlation alone: frame rain, order load, and rider count as context consistent with the observed stage change unless the data establishes more.

If a requested time window spans multiple hour buckets, use the same buckets for both dates and disclose the chosen window. Confirm how the tool defines hourly buckets. If “last night” cannot be resolved from the tool or a configured demo date, ask which dates the manager means.

## Diagnosis from a live queue

A high number of packed orders relative to available riders is evidence of dispatch pressure and likely rider-wait risk. The project playbook gives a warning reference of **above about 2 pending orders per available rider**. This is an approximate warning threshold, not a measured wait-time formula. Compute the ratio only from tool-returned counts and define “available” consistently; exclude riders who are on delivery, on break, offline, or only on standby. Returning riders are not available until the tool reports them available.

Use order-state data to distinguish a store-floor issue from a dispatch queue issue. A large picking queue can motivate checking pick-pack metrics, but queue state alone cannot establish pick-pack duration. Never convert an order count or ratio into an exact ETA without an approved calculation based on returned data.

## Rain and surge interpretation

Rain can coincide with more orders, fewer available riders, longer rider waits, and slower rides. Check the actual period's metrics and rain flag before using this explanation. If rider wait is the largest increase, say so with the measured differences; do not say rain caused every increase without evidence. Recommend safe capacity and customer-expectation levers from DD-WEATHER-001 and DD-COMMS-001. Never respond to slower rides by encouraging faster or riskier riding.

## Response pattern

1. State the period/snapshot and source.
2. Name the leading delayed stage.
3. Give the returned measurements that support the finding and note material secondary changes.
4. Separate measured facts from interpretation.
5. Offer only policy-compliant next-step drafts; do not promise a result.

## Worked-method example (method only)

When comparing a dry hour and a rain-affected hour, subtract each dry-stage duration from its matching rain-stage duration. If the rider-wait increase is larger than the pick-pack and ride increases, describe rider wait as the largest measured contributor. The actual values must come from the metrics tool for the requested period; this guide supplies no current or reusable sample figures.
