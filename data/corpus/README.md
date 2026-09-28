# DispatchDesk RAG corpus

**Status:** Week 1, Task 7 deliverable  
**Document count:** 6 operational documents  
**Format:** UTF-8 Markdown, one retrieval-friendly source per file  
**Intended use:** Chunk each document by its `##` sections; preserve `doc_id`, title, section heading, version, and source metadata on every chunk.

## Source and authority

These are purpose-built, simulated operating documents for the DispatchDesk demo. They normalize the policy statements and thresholds explicitly present in `docs/initial/requirements.md` and the updated `docs/6-pager.md` (including its Dispatch Playbook appendix). They are not evidence of an actual employer's policy, legal advice, or live store state. `docs/initial/sample_data/dispatch_operations_playbook.pdf` is included as source material, but its text was not independently extracted while preparing this version; do not attribute additional rules to it without review.

For the demo, apply this source order:

1. Safety and working-hours limits in `06-rider-safety-and-working-hours.md` are hard constraints.
2. Explicit order, batching, and customer-communication rules in their respective corpus documents apply to proposals.
3. Manager preferences from memory may add stricter constraints; they cannot relax a policy rule. Surface conflicts rather than silently overriding preferences.
4. Current order, rider, stage, weather, and time facts come only from live or historical tools, never from this corpus. Do not treat the memo's worked examples as current facts.

## Inventory and query coverage

| ID | File | Coverage | Required query coverage |
| --- | --- | --- | --- |
| DD-SOP-001 | `01-dispatch-sop.md` | Triage, bottleneck-first diagnosis, dispatch sequence, proposal-only actions | 1, 2, 3, 6 |
| DD-DIAG-001 | `02-delay-diagnosis-guide.md` | Stage definitions, target ranges, evidence-based root-cause comparison | 1, 2, 3 |
| DD-BATCH-001 | `03-batching-and-cold-chain.md` | Eligibility, geography, detour, item/order/age limits, exclusions | 1, 3, 5 |
| DD-WEATHER-001 | `04-rain-and-surge-playbook.md` | Rain pattern, safe capacity levers, surge controls | 1, 2, 3, 4 |
| DD-COMMS-001 | `05-customer-communication.md` | Honest estimates, delay notices, approved message drafts | 3, 4 |
| DD-RIDER-001 | `06-rider-safety-and-working-hours.md` | Safety refusal, penalties, hours, breaks, alternatives | 4, 6 |

## Corpus readiness notes

- The corpus contains six documents covering all six sample queries. It supplies operating rules and response guidance, not live facts.
- Retrieve by section, not by treating each whole file as an indivisible chunk. Keep threshold statements together with their qualifiers and exceptions.
- Several thresholds are approximate (stage targets, rider-wait warning, detour distance/time). They support diagnosis and screening, not a claim of exact prediction.
- The source materials leave the exact 8-minute batch boundary unresolved. Until the team decides otherwise, use the conservative rule in DD-BATCH-001: do not newly batch an order at or beyond 480 seconds; still offer the required proactive delay-notice draft at that point.
- Zone adjacency/detours, incentive cap, and a store closing time are not defined here. Do not infer them. A batch depending on unknown geography is ineligible for a positive recommendation; an incentive proposal needs a manager's stored/approved cap; shift decisions use policy maximums and verified rider hours, not a presumed closing time.
- ETA calculations and queue/rider counts are intentionally excluded. These must be tool-derived in Week 2 and any ETA must be explicitly labeled an estimate, never a promise.

## Query-to-source retrieval map

| Query | Primary sections | Supporting sections |
| --- | --- | --- |
| 1. Backlog now; what first? | DD-SOP-001 triage; DD-DIAG-001 queue interpretation | DD-BATCH-001 eligibility; DD-WEATHER-001 capacity levers; DD-RIDER-001 hours |
| 2. Compare two evenings | DD-DIAG-001 comparison method | DD-WEATHER-001 rain pattern |
| 3. Rain, batching, ETA | DD-BATCH-001 all eligibility checks; DD-COMMS-001 estimates | DD-WEATHER-001; DD-RIDER-001 |
| 4. Speed and dock pay | DD-RIDER-001 prohibited pressure/penalties | DD-WEATHER-001 safe alternatives; DD-COMMS-001 customer expectation setting |
| 5. Store weekend preferences | DD-BATCH-001 preferences cannot relax rules; DD-SOP-001 memory conflict handling | DD-RIDER-001 safety floor |
| 6. Extend rider shift | DD-RIDER-001 maximums and verification | DD-SOP-001 alternatives and approval workflow |

## Change control

When a team decision resolves an open boundary, change the relevant source document and this note together, record the decision/date in version history, then rerun retrieval checks for the affected query. Do not silently change thresholds in prompts or code while leaving corpus text stale.
