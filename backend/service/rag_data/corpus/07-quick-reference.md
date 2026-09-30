# Quick reference: symptom, likely cause, first action, and escalation

**Document ID:** DD-QUICKREF-001  
**Version:** 1.0 (2026-09-29)  
**Authority:** Simulated demo guidance. Primary source: *Dark Store Last-Mile Dispatch Playbook* (sample operations document, illustrative thresholds only), section 7.  
**Applies to:** Fast triage during a peak and deciding when to escalate to regional operations. Each section below is one symptom.

## High rider wait with pending orders above 2 per available rider

**Symptom:** Rider wait is high and there are more than about 2 pending orders per available rider.
**Likely cause:** Too few riders online or free.
**First action:** Dispatch the oldest waiting order first, call in a standby rider, and batch eligible orders under DD-BATCH-001.
**Escalate to regional ops when:** No standby rider is available and rider wait stays high for 15 minutes or more.

## High pick-pack time with orders stuck in picking

**Symptom:** Pick-pack time is high and orders are sitting in the picking state.
**Likely cause:** Picker shortage, a stock-out, or a mis-slotted item.
**First action:** Reassign a picker and check the top-10 items for stock. Adding riders will not fix this.
**Escalate to regional ops when:** A high-volume item is out of stock.

## High ride time with the rain flag on

**Symptom:** Ride time is high and it is raining.
**Likely cause:** Wet roads and slower, safer riding.
**First action:** Show customers a longer, honest ETA, shrink the serviceability radius, and apply the surge incentive within the store's cap. Never ask riders to ride faster.
**Escalate to regional ops when:** There is flooding, or an incentive above the store's cap is needed.

## Rider near the shift or break limit

**Symptom:** A rider is close to the 9-hour shift maximum or has gone nearly 4 hours without a break.
**Likely cause:** Long continuous work.
**First action:** Stop dispatching to that rider and arrange a break or relief, following DD-RIDER-001.
**Escalate to regional ops when:** Relief cannot be arranged.

## Frozen item in a waiting order

**Symptom:** A waiting order contains frozen or ice-cream items.
**Likely cause:** Cold-chain constraint.
**First action:** Deliver it single-drop only, never batched, and dispatch it promptly.
**Escalate to regional ops when:** Not applicable.

## Many outer-zone (Z-C) orders waiting

**Symptom:** A large number of waiting orders are in the outer zone, Z-C.
**Likely cause:** Long rides consuming rider capacity.
**First action:** Pair Z-C orders only with orders in adjacent zones, and consider pausing the outer zone.
**Escalate to regional ops when:** The outer-zone backlog is sustained for 30 minutes or more.
