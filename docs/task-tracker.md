# DispatchDesk task tracker

**Last updated:** 2026-09-27  
**Source plan:** [`tasks.md`](../tasks.md)  
**Requirements:** [`requirements.md`](../requirements.md)

This is the working tracker for the 34 required tasks. The source plan defines each task's full definition of done and evidence requirement. Update the status, the 2–3 people taking the task, the reviewer, and the evidence link here as work progresses. A draft is not complete until its definition of done is met and reviewed.

**Statuses:** To do · In progress · In review · Done · Blocked. Record a blocker and next action in the notes section if a task becomes blocked.

## Week 1 — Foundations, RAG, and UI

| # | Task | Status | People (2–3) / reviewer | Evidence or next action |
| --- | --- | --- | --- | --- |
| 1 | Kickoff: requirements, roles, stack | In progress | To assign | [`team.md`](team.md) records the stack; add people, requirements-read confirmations, and task ownership. |
| 2 | Amazon-style 6-pager | In review | To assign | [`6-pager.md`](6-pager.md) is drafted; team review and agreement pending. |
| 3 | PR/FAQ | To do | To assign | Create `docs/pr-faq.md` and review it. |
| 4 | Repository, branches, `.gitignore`, README | To do | To assign | Create remote repo and verify a fresh clone can run it from README. |
| 5 | System prompt and two manual checks | To do | To assign | Commit prompt and transcripts showing no invented figures and proposal-only actions. |
| 6 | Synthetic dispatch dataset | To do | To assign | Commit queue, rider, and two-evening metrics data with a count summary. |
| 7 | RAG policy corpus | To do | To assign | Commit SOP, delay guide, batching/cold-chain, rain/surge, customer-communication, and rider-policy sources. |
| 8 | Chunking, embeddings, pgvector ingestion | To do | To assign | Save successful run log with expected and actual chunk counts. |
| 9 | Retrieval test on rain-delay question | To do | To assign | Log top-three passages and relevance judgment; confirm embedding model. |
| 10 | Question-to-grounded-answer prototype | To do | To assign | Save one successful transcript with source references. |
| 11 | Gradio chat UI and shareable demo | To do | To assign | Save running UI screenshot and shareable link. |

**Week 1 exit check:** A fresh teammate can run the prototype, ask a rain-delay policy question, inspect relevant retrieved sources, and see that the assistant does not claim current dispatch facts before live tools exist.

## Week 2 — Tools, MCP, and memory

| # | Task | Status | People (2–3) / reviewer | Evidence or next action |
| --- | --- | --- | --- | --- |
| 12 | Specify live-status and historical-metrics tools | To do | To assign | `docs/tools.md` with inputs, outputs, timestamps, and errors. |
| 13 | Implement live-dispatch-status tool | To do | To assign | Known-store and unknown-store test log. |
| 14 | Implement delivery-metrics tool | To do | To assign | Valid-period and invalid-period test log. |
| 15 | Expose tools through MCP | To do | To assign | One trace showing both tool calls and a grounded answer. |
| 16 | Design preference-memory schema | To do | To assign | Schema and write/read evidence. |
| 17 | Recall preferences across two sessions | To do | To assign | Two-session transcript. |
| 18 | Show agent trace in Gradio | To do | To assign | Screenshot with calls, timestamps, and recalled preferences. |

## Week 3 — Guardrails and caching

| # | Task | Status | People (2–3) / reviewer | Evidence or next action |
| --- | --- | --- | --- | --- |
| 19 | Map guardrail rules to requirements | To do | To assign | `docs/guardrails.md`. |
| 20 | Implement response guardrail checks | To do | To assign | Trace of a response being corrected or filtered. |
| 21 | Test unsafe, over-hours, and benign requests | To do | To assign | Three transcripts with expected outcomes. |
| 22 | Cache policy and historical data; bound live-data freshness | To do | To assign | Miss/hit and live refetch logs. |
| 23 | Measure cache hit rate and latency | To do | To assign | Before/after measurements. |
| 24 | Run all six required scenarios end to end | To do | To assign | Expected-versus-actual table. |
| 25 | Show guardrail, freshness, and cache badges | To do | To assign | Screenshots of each state. |

## Week 4 — Observability, evaluation, and demo

| # | Task | Status | People (2–3) / reviewer | Evidence or next action |
| --- | --- | --- | --- | --- |
| 26 | Instrument request traces | To do | To assign | One exported trace with shared ID. |
| 27 | Build six-case evaluation harness | To do | To assign | One-command eval script. |
| 28 | Run and save baseline evaluation | To do | To assign | Timestamped per-case baseline report. |
| 29 | Analyze failures and choose three fixes | To do | To assign | Root-cause and priority table. |
| 30 | Apply fixes and rerun evaluation | To do | To assign | Before/after score report. |
| 31 | Build observability dashboard | To do | To assign | Screenshot or link with real run data. |
| 32 | Handle timeout, ambiguity, retrieval miss, preference conflict | To do | To assign | Four fallback transcripts or traces. |
| 33 | Write and rehearse demo script | To do | To assign | Script and timed rehearsal note. |
| 34 | Deploy, rehearse, and record backup demo | To do | To assign | Deployment and video links in README. |

## Notes and decisions

The embedding model remains a candidate until task 9 validates retrieval. The Week 1 app cannot make claims about the current queue, rider state, SLA, or ETA because the live tools arrive in Week 2. Keep drafts and operational actions subject to explicit manager approval throughout the build.
