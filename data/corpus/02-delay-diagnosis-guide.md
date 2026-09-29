# Delay diagnosis guide: identify which stage is slipping

**Document ID:** DD-DIAG-001  
**Version:** 1.1 (2026-09-29)  
**Authority:** Simulated demo guidance. Primary source: *Dark Store Last-Mile Dispatch Playbook* (sample operations document, illustrative thresholds only), sections 1 and 2.  
**Applies to:** Live backlog explanations and historical delivery-stage comparisons.

## Delivery stages and diagnostic reference points

A delivery's elapsed time is composed of three stages, and delay almost always lives in one of them:

- **Pick-pack:** order placed until the order is packed and ready. Reference target: about 3 minutes.
- **Rider wait:** packed order waits until a rider picks it up. Reference target: about 1.5 minutes.
- **Ride:** rider pickup until delivery. Reference target: about 5 minutes within a 2 km radius.

Average delivery time is the sum of the three stages. These are approximate diagnostic reference points, not guarantees, hard service limits, or predictions for an individual order. A stage above its reference deserves investigation; it does not, by itself, establish the cause.

## Why the SLA percentage falls sharply

The share of orders delivered inside 10 minutes falls off a cliff, not a slope. When average delivery time drifts from roughly 8 minutes to 12 minutes, the share of orders inside 10 minutes can fall from around 90% to under 50%. For this reason, always diagnose by comparing the stage breakdown against a normal evening rather than looking at the SLA percentage alone.

## Common causes by stage

**Rider wait** is the dominant increase when packed orders are stacking up and few riders are free. Check the ratio of pending orders to available riders and compare riders online against a normal evening. Riders dropping off in bad weather while orders rise is the classic pattern.

**Pick-pack** is the dominant increase when orders sit in the picking state, pickers are short, or a popular item is out of stock or mis-slotted. More riders will not fix a pick-pack problem. First actions are to reassign a picker and check the top-10 items for stock.

**Ride time** is the dominant increase when it is raining, there is a traffic event, or a large share of orders are in the outer zone. Ride time rises in wet weather even with no change in rider count. Slower rides are never addressed by asking riders to ride faster.

## Diagnosis from historical metrics

For a comparison, retrieve matching hourly periods for both dates from the delivery-metrics tool. Compare orders, rider count, rain flag, pick-pack, rider wait, ride, average delivery, and SLA where those fields are returned. Calculate stage differences from the returned values, keeping units consistent. Identify the stage with the largest contribution to the increase in average delivery time; describe other contributing stages too. State the data period and actual figures. Do not infer causation from correlation alone: frame rain, order load, and rider count as context consistent with the observed stage change unless the data establishes more.

If a requested time window spans multiple hour buckets, use the same buckets for both dates and disclose the chosen window. Confirm how the tool defines hourly buckets. If "last night" cannot be resolved from the tool or a configured demo date, ask which dates the manager means.

## Diagnosis from a live queue

A high number of packed orders relative to available riders is evidence of dispatch pressure and likely rider-wait risk. The playbook gives a warning reference of **above about 2 pending orders per available rider**, beyond which waits climb quickly. This is an approximate warning threshold, not a measured wait-time formula. Compute the ratio only from tool-returned counts and define "available" consistently; exclude riders who are on delivery, on break, offline, or only on standby. Returning riders are not available until the tool reports them available.

Use order-state data to distinguish a store-floor issue from a dispatch queue issue. A large picking queue can motivate checking pick-pack metrics, but queue state alone cannot establish pick-pack duration.

## Rain and surge interpretation

Rain can coincide with more orders, fewer available riders, longer rider waits, and slower rides. Check the actual period's metrics and rain flag before using this explanation. If rider wait is the largest increase, say so with the measured differences; do not say rain caused every increase without evidence. Recommend safe capacity and customer-expectation levers from DD-WEATHER-001 and DD-COMMS-001.

## Response pattern

1. State the period/snapshot and source.
2. Name the leading delayed stage.
3. Give the returned measurements that support the finding and note material secondary changes.
4. Separate measured facts from interpretation.
5. Offer only policy-compliant next-step drafts; do not promise a result.

## Worked-method example (method only)

When comparing a dry hour and a rain-affected hour, subtract each dry-stage duration from its matching rain-stage duration. If the rider-wait increase is larger than the pick-pack and ride increases, describe rider wait as the largest measured contributor. The actual values must come from the metrics tool for the requested period; this guide supplies no current or reusable sample figures.
