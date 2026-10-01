# DispatchDesk PR/FAQ

## Press release

### DispatchDesk launches to help dark store managers make safe calls when deliveries slip

**Understand where orders are slowing down, review practical options, and keep control of dispatch without putting riders under unsafe pressure.**

**BENGALURU:** DispatchDesk today launched a dispatch copilot for dark store managers facing evening peaks and rain delays. Managers can ask why deliveries are slipping and what they should do first. The assistant brings together the order queue, rider availability, delivery-stage history, and the store's operating rules to explain the situation and propose a safe next step.

For Karthik Reddy, a dark store manager in Koramangala, a falling delivery score starts a scramble. He checks packed orders, calls riders, compares yesterday's numbers, and looks up which orders can be batched. Regional operations wants the delivery promise met, but sending a tired rider back out or combining frozen goods with another delivery can create a bigger problem. Karthik needs an answer he can check and a decision he can explain.

With DispatchDesk, he can ask, “Packed orders are piling up. What should I do first?” The assistant checks the queue and rider states, identifies the oldest waiting work, and explains which options fit the playbook. Frozen-item orders remain single-drop. Standby call-ins, eligible batches, and customer updates appear as proposals for Karthik to approve. When he asks why last night went badly, it compares packing, rider wait, and travel time using the relevant records.

DispatchDesk also remembers his store's alert thresholds and batching preferences across shifts. It refuses requests to make riders speed, skip breaks, or work beyond policy limits, and offers permitted alternatives. Every operational recommendation shows its supporting facts and rules; an unsupported delivery estimate is never presented as a promise.

“During a rush, I need to know what is holding us up and what I can safely change,” says Karthik. “DispatchDesk helps me check the options before I give the instruction.”

“We built DispatchDesk to help the manager make a clear call with the evidence in front of them,” says the DispatchDesk team. “Rider safety and the manager's approval are part of that decision.”

Managers and reviewers can explore the experience at [DispatchDesk](https://supply-chain-bv-production.up.railway.app) using prepared synthetic store situations. The project demonstration simulates dispatch changes and does not contact real riders or customers.

## Customer FAQs

### 1. What does DispatchDesk add to my dashboard?

It helps you work through a decision using the queue, riders, delivery history, and playbook together. You can ask what is holding up packed orders or why one evening was worse than another, inspect the supporting facts, and ask a follow-up. The goal is to give Karthik a practical next step he can assess without assembling the explanation across several sources himself.

### 2. Can it help me batch orders in the rain and tell customers when to expect delivery?

It checks order age, item count, geography, detours, cold-chain restrictions, and rider eligibility before proposing a batch. Frozen-item orders must travel separately, and excluded orders are explained. An ETA is given only when operational data supports it, as a labeled estimate range with its data basis and observation time. A batching decision or the ten-minute service promise does not establish an order's arrival time.

### 3. Will it change dispatch or send a message without asking me?

No. Assignments, batches, incentives, service-area changes, standby call-ins, and messages are drafts requiring explicit approval. The proposed action must still fit the current situation before simulated execution. Approval cannot waive safety or cold-chain rules. In this project, execution affects only synthetic data.

### 4. Will I have to repeat my store preferences every shift?

You can save an alert threshold and its applicable time window, batching restrictions, and an incentive cap for the same manager and store. DispatchDesk confirms the values and applies them in a later session. It surfaces conflicts rather than silently overriding a preference. Preferences may be stricter than policy, but cannot relax mandatory rules. Background monitoring and unsolicited alerts are outside the core project.

### 5. What happens to the data I enter?

Use synthetic information for the demonstration. External embedding and language-model providers process text needed for retrieval and answering, including questions, conversation context, and policy passages. The intended completed system also uses relevant synthetic tool results and preferences to support its answers. Real customer or rider information should not be entered without established access and retention controls.

Today, Jina receives retrieval queries, recent conversation context for follow-ups, and corpus text during ingestion. Groq receives the question, session history, retrieved passages, and system prompt. Current scenario database rows are not supplied to chat.

## Internal and guardrail FAQs

### 6. How should DispatchDesk respond to unsafe rider pressure or over-hours requests?

It must refuse to encourage or draft instructions to speed, break traffic rules, skip mandatory breaks, or work beyond the shift limit. It must also refuse pay penalties for weather- or safety-related delays. Rider-specific decisions require verified hours and last-break information from the rider-status tool, not an assumption or an unsupported assertion in the question.

The answer should cite the relevant policy and offer permitted options, such as standby capacity, eligible batching, reduced serviceability, or an honest customer update. Manager approval cannot override these restrictions. The demonstration uses illustrative playbook limits, not a claim about employment law.

### 7. What should happen when information is missing, stale, or ambiguous?

DispatchDesk should state what could not be verified and avoid inventing counts, rider states, timings, or ETAs. An older snapshot must show its observation time and must not be presented as current. Missing routing or rider information prevents a specific recommendation that depends on it. References such as “that rider” or “last night” need clarification when the available records cannot resolve them. General policy guidance remains useful when operational data is unavailable.

### 8. How do we check that the answers follow the requirements?

Use the six queries in the [requirements](initial/requirements.md): backlog, evening comparison, rain batching and ETA, unsafe rider pressure, preference recall, and a rider nearing the shift limit. Check that operational claims trace to tool results, policies are retrieved and applied, excluded orders are explained, preferences persist across two sessions, and every action remains a proposal until approved.

Also test unavailable data and benign questions about breaks. A refusal count alone does not show that the assistant is helpful. Record the evidence and actual response for each case; do not claim measured delivery improvements or universal safety from a finite demonstration.

### 9. Does the mock announcement describe the application available today?

It describes the intended completed DispatchDesk experience. The current prototype has policy RAG chat, session history, scenario loading, and database-backed data inspection. Operational tools, persistent preferences, a separate guardrail layer, and simulated approval/execution are planned capabilities. This Week 1 document defines the customer promise the later work must deliver.

*Review status: draft for whole-team review and agreement, as required by Task 3.*
