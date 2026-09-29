# DispatchDesk RAG corpus

**Status:** Week 1, Task 7 deliverable (revision 1.1, 2026-09-29)  
**Document count:** 7 operational documents  
**Expected chunk count:** 37 (one chunk per `##` section, README excluded)  
**Format:** UTF-8 Markdown, one retrieval-friendly source per file  
**Intended use:** Chunk each document by its `##` sections. Text before the first `##` is header metadata, not chunk content. Preserve `doc_id`, title, section heading, version, and file on every chunk. **Do not ingest this README.**

## Source and authority

These are purpose-built, simulated operating documents for the DispatchDesk demo. The primary source is the *Dark Store Last-Mile Dispatch Playbook* (`docs/initial/sample_data/dispatch_operations_playbook.pdf`), a sample operations document whose thresholds are illustrative only. They are not evidence of an actual employer's policy, legal advice, or live store state. Where the playbook leaves a boundary unresolved, the team's interpretation is recorded in the decision log below.

Source order for the demo:

1. Safety and working-hours limits in `06-rider-safety-and-working-hours.md` are hard constraints.
2. Explicit order, batching, and customer-communication rules in their respective documents apply to proposals.
3. Manager preferences from memory may add stricter constraints; they cannot relax a policy rule. Conflicts are surfaced rather than silently resolved.
4. Current order, rider, stage, weather, and time facts come only from live or historical tools, never from this corpus.

Rules about the assistant's own behavior (no invented figures, draft-only actions, handling missing data) live in the system prompt and guardrail layer, not in this corpus. See `notes/rules-moved-to-system-prompt.md`.

## Inventory and query coverage

| ID | File | Sections | Coverage | Sample queries |
| --- | --- | --- | --- | --- |
| DD-SOP-001 | `01-dispatch-sop.md` | 4 | Triage, dispatch priority, draft-only workflow, preference handling | 1, 2, 3, 5, 6 |
| DD-DIAG-001 | `02-delay-diagnosis-guide.md` | 8 | Stage targets, SLA cliff, causes by stage, historical and live diagnosis | 1, 2, 3 |
| DD-BATCH-001 | `03-batching-and-cold-chain.md` | 5 | Batching purpose, eligibility checks, exclusions, preferences | 1, 3, 5 |
| DD-WEATHER-001 | `04-rain-and-surge-playbook.md` | 5 | Heavy-rain expectations, levers in playbook order, surge exception, prohibited responses | 1, 2, 3, 4 |
| DD-COMMS-001 | `05-customer-communication.md` | 4 | Honest estimates, delay notices, credits, message drafts | 3, 4 |
| DD-RIDER-001 | `06-rider-safety-and-working-hours.md` | 5 | Safety rules, penalties, hours, breaks, refusals and alternatives | 4, 6 |
| DD-QUICKREF-001 | `07-quick-reference.md` | 6 | Symptom → cause → first action → escalation trigger | 1, 2, 3, 4, 6 |

## Query-to-source retrieval map

| Query | Primary sections | Supporting sections |
| --- | --- | --- |
| 1. Backlog now; what first? | DD-SOP-001 triage; DD-QUICKREF-001 high rider wait | DD-DIAG-001 live queue; DD-BATCH-001 eligibility; DD-WEATHER-001 levers |
| 2. Compare two evenings | DD-DIAG-001 historical metrics, causes by stage | DD-WEATHER-001 heavy-rain expectations; DD-DIAG-001 SLA cliff |
| 3. Rain, batching, ETA | DD-BATCH-001 eligibility; DD-COMMS-001 estimates | DD-WEATHER-001 levers; DD-QUICKREF-001 high ride time |
| 4. Speed and dock pay | DD-RIDER-001 non-negotiable rules | DD-WEATHER-001 prohibited responses and levers; DD-COMMS-001 |
| 5. Store weekend preferences | DD-BATCH-001 preference interaction; DD-SOP-001 memory handling | DD-RIDER-001 safety floor |
| 6. Extend rider shift | DD-RIDER-001 maximum shift and break | DD-QUICKREF-001 rider near limit; DD-SOP-001 draft-only workflow |

Task 9 test query ("why are my deliveries slipping when it rains?") should retrieve DD-WEATHER-001 *What to expect in heavy rain*, DD-DIAG-001 *Common causes by stage*, or DD-QUICKREF-001 *High ride time with the rain flag on* in the top 3.

## Decision log

| Date | Decision | Affects |
| --- | --- | --- |
| 2026-09-28 | Batching age limit: do not newly batch at or beyond 480 seconds; delay notice also prepared at 480 seconds or later | DD-BATCH-001, DD-COMMS-001 |
| 2026-09-28 | Detour cap: require both ≤ ~0.8 km and ≤ ~2 min; reject if either exceeds or is unknown | DD-BATCH-001 |
| 2026-09-28 | Rain alone does not declare a surge; the manager must declare it | DD-WEATHER-001 |
| 2026-09-29 | Restored playbook content missing from v1.0 (SLA cliff, causes by stage, rain expectations, rider protections, incentive sign-off); added quick reference; moved assistant-behavior rules to the system prompt | All |

## Known data gaps

Zone adjacency and detours, the incentive cap, and a store closing time are not defined in the corpus or the sample dataset. Until they are added to the dataset, a batch depending on unknown geography cannot be recommended, an incentive proposal needs a recorded cap, and shift decisions use policy maximums and verified rider hours.

## Change control

When a team decision resolves an open boundary, change the relevant source document and this README together, add a row to the decision log, then rerun retrieval checks for the affected query. Do not change thresholds in prompts or code while leaving corpus text stale.
