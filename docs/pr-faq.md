# DispatchDesk PR/FAQ

**Working Backwards draft, 1 October 2026.** The press release and customer FAQs describe the intended completed DispatchDesk demonstration at a future launch. They do not announce capabilities available in today's application. The store, persona, and quotes are illustrative. The launch date remains an open decision. Internal FAQs identify what must be built and proved before that announcement is credible.

## Press release

### DispatchDesk helps dark store managers understand delivery delays and choose a safe next step

**A browser assistant brings the queue, rider availability, delivery history, and store rules into one conversation, with every dispatch change left for the manager to approve.**

**BENGALURU, [launch date]:** DispatchDesk today introduced a dispatch copilot for dark store managers facing evening peaks and rain delays. Managers can ask what is holding up orders, see the evidence behind the answer, and review a practical next step. The guided demonstration recreates these decisions in a simulated store, showing how managers can keep control of dispatch while working through delivery pressure.

A falling delivery score leaves the manager with several possible problems and little time to distinguish them. Orders may still be getting packed, packed bags may be waiting for riders, or journeys may be taking longer. The manager must piece together queue screens, rider availability, yesterday's numbers, and the operating playbook before deciding what to change. A hurried response can target the wrong bottleneck or put a tired rider back on the road.

DispatchDesk brings those checks into the conversation. When Karthik, a manager in Koramangala, asks, “Packed orders are piling up. What should I do first?”, the assistant checks the simulated queue and rider roster, identifies the oldest waiting work, and explains the constraints on each option. It distinguishes orders that could share a rider from frozen-item orders that must travel separately. A recommendation to use standby capacity or prepare a customer update appears with its supporting facts and policy, ready for Karthik to review.

The same conversation helps Karthik understand yesterday's slowdown and carry his preferences into the next shift. He can compare time spent packing, waiting for riders, and travelling, then ask how the findings change his next decision. His saved thresholds and batching restrictions apply when he returns. If a request would pressure riders to speed, skip a break, or work beyond the shift limit, DispatchDesk explains the rule and offers permitted alternatives. It does not make a delivery promise from an unsupported estimate.

“The manager should be able to see why a next step makes sense before asking anyone to act,” says the DispatchDesk team. “We bring the evidence and the operating rules together, and keep the decision in the manager's hands.”

“I used to see the delivery score falling and start calling people to work out what was wrong,” says Karthik Reddy, a dark store manager in Koramangala. “Now I can check what is waiting, see which options fit our rules, and explain the call I'm making.”

At launch, managers and reviewers can open [DispatchDesk](https://supply-chain-bv-production.up.railway.app) in a browser, choose a prepared store situation, inspect its records, and ask their own dispatch questions. The project team will support guided walkthroughs and review of simulated proposals. No demonstration action contacts a real rider or customer or changes a real order.

## Customer FAQs

These answers describe the same future demonstration announced above.

### 1. Who is DispatchDesk for?

Dark store managers who must decide what to do when deliveries slip, especially during evening demand and rain. It is for questions that require combining current dispatch information with operating rules: which constraint is active, what options are allowed, and what should be checked before acting. The demonstration supports English and uses a prepared synthetic store.

### 2. What does it add to my dispatch dashboard?

You can ask a question that crosses the queue, riders, delivery history, and policy without assembling that explanation yourself. The answer links the observed situation to the relevant rule and a proposed next step. You can inspect the evidence and ask a follow-up. Your dashboard remains useful for monitoring; DispatchDesk helps you work through a decision.

### 3. How do I know where an answer came from?

Operational figures carry the snapshot time or historical period they describe. Policy recommendations cite the relevant playbook passages. Derived figures show their inputs and calculation. If the evidence is incomplete, DispatchDesk names the missing check rather than presenting a possible explanation as a confirmed cause. You can inspect the evidence used for that answer.

### 4. Can it tell me why yesterday was worse or which orders to batch in the rain?

It compares matching periods across evenings and shows how packing, rider wait, and travel changed alongside demand, riders online, and weather. For a batch proposal, it checks order age, item count, cold-chain restrictions, geography, detours, and rider eligibility. Frozen-item orders go separately. If verified inputs support a delivery estimate, it presents a labeled range with its data basis; otherwise it says an estimate is unavailable.

### 5. Will it send instructions or change dispatch by itself?

No. Assignments, batches, incentives, service-area changes, standby call-ins, and messages remain proposals until you approve the specific action. If conditions change, the proposal is checked again before simulated execution. Approval cannot override a safety or cold-chain rule. In this demonstration, execution changes only synthetic state; it does not contact people or operate real systems.

### 6. What if I ask riders to speed, skip a break, or accept a pay penalty for delays?

It will not draft requests to speed, break traffic rules, skip mandatory breaks, exceed shift limits, or dock pay for weather- or safety-related delays. It explains the applicable policy and offers permitted options. Rider-specific advice requires verified hours and break information. The demo's working-hours limits come from its illustrative playbook, not a statement about employment law or your operator's policy. Real use would require your operator's authoritative rules.

### 7. Will it remember my preferences next shift?

Yes. You can save an alert threshold and its time window, batching restrictions, and an incentive cap for the same manager and store. DispatchDesk confirms the saved values and applies them in a later session. Conflicts are shown for review, and preferences cannot loosen mandatory policy. Thresholds are checked during your interactions; background monitoring and unsolicited alerts are outside this release.

### 8. What happens when data is missing, old, or unavailable?

The answer identifies what could not be verified. An older snapshot may be shown with its observation time, but is not described as the current queue. An unsupported ETA, rider assignment, or batch is withheld. An ambiguous reference prompts a question before a decision depends on it. You can still discuss general policy when operational information cannot be reached.

### 9. What information leaves the application?

The demonstration uses synthetic records. External embedding and language-model providers receive the text needed for retrieval and answering; that can include questions, conversation history, policy passages, and selected synthetic operational context. Persistent preferences are scoped to the demonstration's manager and store. Do not enter real customer or rider information. The internal FAQ describes the current data flow and the controls needed before any real-store trial.

### 10. How do I get started, get help, and find the price?

At launch, open DispatchDesk, choose a prepared store situation, inspect its records, and ask a dispatch question. The team supports the walkthrough and collects feedback. There is no commercial subscription offer in this release; production pricing, onboarding, and ongoing support have not been decided.

## Internal FAQs

### 1. What decision is this document asking us to make?

Agree on the customer experience the completed project must demonstrate, then determine whether it is worth delivering. The proposed benefit is less work assembling evidence for a dispatch decision while retaining manager control and rider protections. The press release is a destination. It does not establish that the benefit has been measured or that a production service is ready.

The immediate commitment is to finish and evaluate the simulated copilot against the six required interactions. A real-store integration or commercial launch needs a separate decision. A launch date must follow confirmed capacity and acceptance evidence; this draft does not create a calendar commitment.

### 2. Who would use it, who would pay, and what evidence supports the problem?

The on-shift manager is the intended user. An operator or regional operations team is a possible buyer, but neither purchasing authority nor willingness to pay has been validated. Karthik's store and operating pressures come from the [requirements](initial/requirements.md). They are design assumptions, not customer interviews or field measurements.

We need to observe how actual managers diagnose delays, what information they already have together, how often they must switch sources, and what mistakes or delays matter. Until then, we cannot claim a market size, adoption forecast, management-time saving, or delivery improvement. Estimating demand requires the number of operators with this problem, their data readiness, integration cost, and willingness to pay.

### 3. Why would a manager change their existing workflow?

The proposed advantage is an explanation and checked next step assembled from operational evidence and policy in one interaction. Existing alternatives include reading a dashboard and SOP, calling colleagues, searching policy, or using a rules-based dashboard. A general chat model may answer quickly but lacks verified store evidence unless it is supplied.

We should compare these alternatives using the same records and rules. If the dashboard already makes the cause and safe action clear, or the conversation adds latency without improving the decision, the assistant has not earned its place. Both the burden of assembling evidence and a manager's willingness to consult chat during a peak are unvalidated adoption assumptions. The claimed benefit must survive that comparison rather than depend on enthusiasm for chat.

### 4. What is built today, and what separates it from the launch promise?

Today the Gradio application provides policy RAG with Jina embeddings, PostgreSQL/pgvector retrieval, Groq answers, and conversation history within a session. Separate demo controls load normal, backlog, and rain YAML snapshots into four operational tables and display fresh saved rows. Scenario loading replaces the shared operational dataset; it is not isolated per chat session.

Chat does not receive those operational rows. There are no connected dispatch tools, persistent store preferences, response evidence panel, simulated approval/execution workflow, deterministic response guardrail layer, cache, or observability dashboard. Those are delivery requirements for the future experience. The site linked in the release currently hosts this policy-chat prototype; the same address is intended for the completed demonstration. The three fixture names are starting datasets, not proof that the six required interactions work.

### 5. What are the hardest problems, and how do we propose to solve them?

| Problem | Proposed approach | Evidence needed |
| --- | --- | --- |
| Facts can be stale or inconsistent | Tools return a consistent store snapshot and observation time; define a freshness limit and explicit unavailable-data behavior. | Concurrent edits and timeouts do not produce unsupported current-state claims. |
| The model can invent figures or omit a constraint | Code calculates stage differences and checks rider hours, breaks, batch eligibility, and supported ETA inputs; validate the response against that evidence. | Grounding and safety checks pass direct, indirect, and benign requests. |
| An approved proposal can become invalid | Associate approval with a specific proposal and revalidate current state immediately before a transactional simulated change. | Changed data invalidates an incompatible approval; no action occurs without approval. |
| Preferences can leak or be forgotten | Persist preferences under manager and store identity, confirm writes, and retrieve them in later sessions. | Cross-session recall, conflict handling, and identity isolation are demonstrated. |
| A retrieved passage can be relevant but incomplete | Evaluate retrieval and the resulting answer together, including excluded orders and missing policy inputs. | Required rules appear and are applied correctly across varied questions. |

The corpus provides illustrative demo policy. Synthetic route assumptions are not map verification, and historical hourly values are supplied aggregates. Missing ETA or routing inputs must yield an explicit limitation, not a fabricated estimate.

### 6. What will we deliberately leave out?

Real order-management, rider, payment, messaging, and maps integrations; autonomous dispatch; real-world route optimization; demand forecasting; and background alerts. Demo approvals simulate an action only. We do not claim that this work proves improved real-store SLAs or that prompt instructions alone guarantee safe outputs.

Policy guidance remains available when operational tools fail. Specific operational advice remains dependent on verified inputs. Additional features should not delay these basic boundaries.

### 7. How will we know the launch experience works?

| Required interaction | Acceptance evidence |
| --- | --- |
| Backlog | Fresh queue and rider facts, oldest-order age, and policy-supported priorities presented as proposals. |
| Two evenings | Matching periods and correct stage differences, with order volume, rider availability, and rain as context; measured changes are distinguished from possible causes. |
| Rain, batches, and ETA | Eligible pairs checked against every rule, exclusions explained, and an estimate range only when its inputs support it. |
| Unsafe speed and pay pressure | Prohibited instructions are refused without drafting them; compliant alternatives are offered. |
| Store preferences | Values confirmed in one session and correctly recalled for the same manager and store in a second, with conflicts surfaced. |
| Rider near a limit | Hours and last break verified; work beyond policy limits declined with alternatives. |

Also test timeouts, ambiguous references, missing policy, preference conflicts, stale data, and benign break questions. Record inputs, model settings, retrieved sources, tool results, proposed changes, and per-case outcomes. An invented operational figure, unsafe recommendation, unapproved action, or silent preference override blocks technical acceptance. Passing this finite set establishes behavior under those conditions, not a universal safety guarantee.

### 8. How will we test whether the assistant is useful rather than just functional?

After technical acceptance, run a supervised comparison with actual managers using matched scenarios. Compare DispatchDesk with direct access to the same dashboard records and policies. Measure correct compliant decisions, decision time, evidence inspection, and understanding of uncertainty. Record sample sizes and conditions before drawing conclusions.

Teammate role-play can improve the demo but cannot establish customer adoption. If managers reach equally good decisions more easily with the existing workflow, simplify the product or change the interface before investing in integrations.

### 9. What dependencies and data controls are required?

The current implementation depends on Gradio, Jina, Groq, and PostgreSQL/pgvector. Retrieval sends Jina the query text, including the recent exchange for follow-ups; ingestion sends corpus text. Groq receives the system prompt, retrieved passages, current question, and session history. Current scenario rows are not sent to either provider by chat.

The future demo adds selected tool results and preferences to response context. Before that change, define what each provider receives, what is logged, who can inspect logs, and how long session, preference, and trace data is retained. Manager/store identity and access checks are prerequisites for persistent memory. All evaluation inputs remain synthetic.

A real-store trial additionally requires operator permission, authoritative policy, suitable data contracts, provider and retention review, and access controls for customer and rider information. None is established by a successful simulated demo. Provider outages and rate limits require a clear error or evidence-backed fallback, never an invented answer.

### 10. What investment and economics would make this worth continuing?

The five-person project team is the current development resource. There is no agreed commercial price, validated effort estimate, or approved launch budget. Estimate work by dependency and measure actual effort; the source plan's task durations are not a delivery guarantee.

Measure cost per completed interaction from embedding and model requests, retries, hosting, database usage, and support. For a future real-store service, add onboarding, integration, policy maintenance, and incident-response costs. Compare that with a buyer's demonstrated willingness to pay and measured workflow value. Free-tier availability does not establish sustainable unit economics.

### 11. Who owns delivery, and what are the checkpoints?

Vishnu Mohan and Ravisekhar R own corpus preparation, chunking, embeddings, ingestion, retrieval, grounded answers, and the 6-pager. Sunny Gupta, Priya Ranjan, and Sharad Nailwal own setup, database and scenarios, application integration, deployment, documentation, and the planned tools, memory, approval, guardrail, caching, observability, and evaluation work.

Follow the [source plan](initial/tasks.md) through policy RAG, operational tools and memory, safety checks and caching, then evaluation and observability. Each stage needs reviewable evidence before the next depends on it. Confirm effort, provider capacity, and the demo launch date at those checkpoints.

### 12. What would make us stop or change the idea?

Pause launch if any critical grounding, safety, approval, or preference requirement fails. Narrow the promise if the available data cannot support it. Reconsider conversation if managers cannot verify the advice, do not find the combined evidence useful, or prefer a simpler dashboard. Do not proceed to real-store use without data rights, access controls, and authoritative policy.

The next review should settle the demonstration scope, delivery estimate, launch date, and technical acceptance evidence. A production investment decision additionally needs manager research and a credible cost model. The release should be revised when those findings change the proposed customer experience.

## Method references

This draft uses the customer-first future-launch framing and separate customer/internal questions described in the [Working Backwards PR/FAQ guidance](https://workingbackwards.com/resources/working-backwards-pr-faq/). Its internal questions examine delivery risks and assumptions using [Bill Carr's FAQ guidance](https://workingbackwards.com/blog/how-to-craft-effective-faqs-in-the-amazon-pr-faq-process/). The press release structure follows [AWS guidance on writing a future press release](https://docs.aws.amazon.com/prescriptive-guidance/latest/oca-framework-align-leaders/business-case.html).
