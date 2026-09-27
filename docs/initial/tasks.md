# DispatchDesk: 4-Week Task Plan
*Core path: 34 one-hour tasks; this is the safe, required build every team should be able to finish. Stretch Goals (bottom) are optional add-ons for teams with extra time.*

## Week 1: Foundations, RAG & UI (11 tasks)
**Demo Goal:** A live Gradio chat UI that answers a manager's "why are deliveries slipping?" question with a RAG-grounded explanation; no tools, memory, or guardrails yet, but it's clickable and shareable. Plus two written deliverables: an Amazon-style 6-pager and a PR/FAQ.

| # | Task (~1 hr) | Definition of Done | Evidence of Completion |
|---|---|---|---|
| 1 | Kickoff: assign roles, review requirements.md and Karthik's persona/objective, agree on tech stack | Roles assigned (prompt/RAG, tools/MCP, memory, guardrails/caching, observability/UI owners); requirements.md read by everyone; stack agreed | A `docs/team.md` listing roles and stack, with each member confirming they've read requirements.md |
| 2 | Write an Amazon-style 6-pager for DispatchDesk: narrative memo covering the problem, the customer (Karthik), the solution, goals & non-goals, key risks/mitigations, and success metrics | 6-pager committed as a narrative document (no slides/bullets-only sections); every section from the standard format is present and specific to DispatchDesk, not generic | `docs/6-pager.md` in repo, reviewed and agreed on by the whole team |
| 3 | Write a PR/FAQ for DispatchDesk: a mock press release announcing the launch, plus an FAQ covering customer questions and internal/guardrail questions | PR/FAQ committed; press release is written from the customer's (Karthik's) point of view; FAQ has at least 5 questions, including at least one on data handling and one on rider safety/working-hours policy | `docs/pr-faq.md` in repo, reviewed and agreed on by the whole team |
| 4 | Set up the git repository: initialize repo, agree on branch strategy, add .gitignore, write a README | Repo exists remotely with main + feature branches; README lets a fresh clone run the project | A teammate clones the repo and runs it successfully from README alone |
| 5 | Draft the system prompt: dispatch-manager tone, "never invent a number or ETA" rule, "propose, never execute" rule, "never pressure riders" rule | Prompt file committed; 2 manual test prompts confirm the agent avoids invented figures and frames actions as proposals | Prompt file in repo + pasted transcript of the 2 test runs |
| 6 | Generate a synthetic dataset of live orders, rider statuses, and hourly delivery-stage metrics (pick-pack, rider wait, ride time) | Dataset file committed covering a live queue snapshot, a rider roster with hours/breaks, and at least two evenings of hourly metrics including one rain-affected evening | Dataset file in repo + a summary count of orders/riders/hours of metrics |
| 7 | Prepare the RAG corpus: dispatch SOPs, delay root-cause guide, batching and cold-chain rules, rain/surge playbook, rider safety and working-hours policy | Corpus covers all 6 sample queries in requirements.md, especially the delay diagnosis, batching rules, and the rider-safety policy | Corpus files committed with document count |
| 8 | Build the ingestion pipeline: chunk and embed the corpus into a vector store | Pipeline runs with no errors; vector store has the expected chunk count | Console log showing chunk/embedding count |
| 9 | Implement retrieval and test against "why are my deliveries slipping when it rains?" | Relevant delay-diagnosis and rain-playbook chunk(s) appear in the top-3 retrieved results | Logged query + retrieved chunks with a correct/incorrect judgment |
| 10 | Wire a minimal prototype: manager question → grounded explanation (no tools yet) | Full query→explanation round trip runs without crashing and reflects the corpus content | Terminal/notebook transcript of one successful run |
| 11 | Build a Gradio chat UI for the prototype and deploy it locally with a shareable link | Gradio app launches and returns a grounded explanation for a real query | Screenshot of the running UI + shareable link posted to the team channel |

## Week 2: Tools, MCP & Memory (7 tasks)
**Demo Goal:** The same Gradio UI now pulls the live queue and rider status and historical stage metrics, and remembers the manager's alert threshold and batching constraints across two shifts; visible live in the chat.

| # | Task (~1 hr) | Definition of Done | Evidence of Completion |
|---|---|---|---|
| 12 | Design tool specs: `get_live_dispatch_status(store_id)` and `get_delivery_metrics(store_id, period)` | Written spec for both tools: inputs, outputs (including an "as of" timestamp on live data), error cases | `docs/tools.md` with both signatures and example input/output |
| 13 | Implement the live-dispatch-status tool | Returns correct queue depth, oldest-order age, per-order details (zone, items, frozen flag), and rider states (with hours on shift and minutes since last break) for a known store, and a clear error for an unknown one | Test log showing both cases |
| 14 | Implement the delivery-metrics tool | Returns correct hourly orders, SLA %, stage breakdown (pick-pack, rider wait, ride), and riders online for a known period, and a clear error for an invalid one | Test log showing both cases |
| 15 | Set up MCP to expose both tools to the agent; test a full round trip | Agent calls both tools via MCP and uses their results in a live response | Trace/log of one query showing the response built from tool output |
| 16 | Design the memory schema: alert threshold (pending orders per available rider + applicable window), batching constraints (e.g., cold-chain rule), incentive cap, and shift handover notes | Schema documented; a record can be written and read back correctly | Schema doc + log of one record written and retrieved |
| 17 | Integrate memory; test preference recall (e.g., "weekends after 7pm, alert above 2 orders per rider; never batch frozen") across 2 sessions | Preferences stated in session 1 are correctly recalled, unprompted, in session 2 | Transcripts of both sessions showing the preferences and their recall |
| 18 | Wire tools and memory into the Gradio UI via an expandable "agent trace" panel | Panel lists each tool call (with data "as of" time) and the recalled preferences for the response | Screenshot of the panel expanded on a real query |

## Week 3: Guardrails & Caching (7 tasks)
**Demo Goal:** In the live UI, show the agent refuse a "make riders speed and dock their pay" request and decline to keep an over-hours rider on shift, and show a visible speed-up (cache hit badge) on a repeated historical-metrics query.

| # | Task (~1 hr) | Definition of Done | Evidence of Completion |
|---|---|---|---|
| 19 | Codify guardrail rules: no invented numbers/ETAs, no unsafe-rider pressure or penalties, no over-hours scheduling, propose-never-execute, no silent override of stored preferences, cold-chain/batching compliance | Rules written as a checklist mapped to requirements.md's guardrail section | `docs/guardrails.md` listing each rule with its requirements.md reference |
| 20 | Implement guardrail checks verified against live tool output and the policy corpus | Every ETA, dispatch proposal, and rider-related answer passes through the guardrail check before reaching the user | Log entry showing a response being reframed/filtered by the guardrail layer |
| 21 | Test guardrails against "make riders hit 10 minutes no matter what, dock their pay" and "keep Farhan on till close" | Both are correctly refused/reframed with an explanation and compliant alternatives; a benign query (e.g., "which riders are due a break?") is not falsely blocked | Transcripts of both test runs plus the benign query |
| 22 | Implement caching for RAG embeddings and historical metrics lookups, with a short TTL (or no caching) for live dispatch status | Repeated identical historical queries hit the cache; live status is never served older than the TTL and is labeled with its "as of" time | Log showing a cache miss then a cache hit on the repeat, and a live-status refetch after TTL expiry |
| 23 | Measure cache hit rate and latency improvement | Latency compared for cached vs. uncached calls with documented improvement | Before/after latency numbers committed to the repo |
| 24 | Run all 6 sample queries from requirements.md end-to-end; fix bugs | All 6 run and are compared against the expected-answers table | Filled-in expected-answers table with actual output and pass/fail per row |
| 25 | Surface guardrail status, data freshness ("as of"), and cache hit/miss as visible badges in the Gradio UI | UI visibly shows guardrail refusals/reframes, data freshness, and cache hits | Screenshots showing all badge states |

## Week 4: Observability, Evals & Demo Readiness (9 tasks)
**Demo Goal:** Full live walkthrough: Gradio UI + observability dashboard, an eval score shown before/after your error-analysis fixes, and an unsafe-rider-pressure refusal on demand.

| # | Task (~1 hr) | Definition of Done | Evidence of Completion |
|---|---|---|---|
| 26 | Instrument observability: log retrievals, tool calls, guardrail triggers, stale-data events, and tool failures | Every event for one request shares a single trace ID | Exported trace for one request showing all event types tied together |
| 27 | Build an eval harness from the expected-answers table with pass/fail scoring | Each of the 6 rows is an automated test case with a scorer | Eval script committed, runnable with one command |
| 28 | Run the eval suite against the synthetic dispatch data; record baseline scores | Suite runs successfully and produces a baseline score | Saved baseline report (score, timestamp, per-case pass/fail) |
| 29 | Do error analysis: categorize failures, find root causes, pick top 3 fixes | Every failing case is categorized (retrieval miss, tool error, guardrail miss, fabricated figure/ETA, unsafe suggestion, latency) with a root cause and prioritized fix | Error-analysis table committed |
| 30 | Apply the top fixes and re-run the eval suite; record the improvement | Score improves measurably over baseline after the fixes | Before/after eval report showing the score delta |
| 31 | Build a dashboard: tool-call failure rate, guardrail trigger count, stale-data count, preference-recall accuracy | Dashboard shows real data and is reachable from the UI | Screenshot/link of the live dashboard with real run data |
| 32 | Handle edge cases: dispatch-status API timeout, ambiguous references ("that rider", "the late order"), no matching corpus content, stored preference conflicting with a suggested action | Each edge case produces a graceful fallback instead of a crash (e.g., stale snapshot clearly labeled, clarifying question, conflict surfaced to the manager) | Log/transcript of each edge case being triggered and handled |
| 33 | Prepare the demo script: Karthik persona, 2-3 live queries, a memory demo, the scorecard | Script covers all elements and is timed to the demo slot | Script document + timed rehearsal note |
| 34 | Final rehearsal, deploy the demo build, record a backup demo video | Live demo runs end-to-end without failure; build deployed and reachable; backup video exists | Deployment link + backup video link, both in README |

## Stretch Goals (optional; the core path above is the safe, required build)
- Baseline comparison: run the same requests through a vanilla LLM with no RAG/tools/guardrails, and show side-by-side why grounded, guardrailed dispatch guidance matters (especially on invented ETAs and unsafe suggestions).
- Red-team your own agent: try to get it to pressure riders, schedule an over-hours rider, or batch frozen items anyway (misleading phrasing like "just motivate them", "he volunteered", indirect requests), then harden the guardrail against what worked.
- Add a "what-if" simulator: let the manager model the effect of calling in one more rider, shrinking the radius, or batching a set of orders on estimated rider-wait time (educational estimate only, clearly labeled as non-predictive).
- Add a proactive alert mode: the agent watches the live queue on an interval and pings the manager when the stored alert threshold is crossed, with a proposed first action.
- Set and hit a latency/cost budget (e.g., under 3s and under $0.01/query) and show the before/after numbers.
- (Add your own ideas here as the team comes up with them.)
