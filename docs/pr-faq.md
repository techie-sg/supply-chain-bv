# DispatchDesk PR/FAQ

## Press release

### DispatchDesk launches to take the guesswork out of the evening dispatch rush

**Dark store managers can understand why orders are falling behind and choose what to do next, while keeping riders safe and control of dispatch in their hands.**

BENGALURU — DispatchDesk today announced its dispatch assistant for dark store managers facing a growing queue of late deliveries. Managers can ask what is holding up orders and get a next step they can check against the situation in their store. By bringing the explanation and the supporting evidence together, DispatchDesk helps managers make a decision they can explain to the people waiting for their instructions.

During the evening rush, a manager's phone can start ringing just as rain slows the riders and packed orders begin to pile up. The dispatch screen shows that deliveries are late, but the manager still has to work out where the delay starts and which intervention will help. Calling for more riders is little use when orders are still being packed. Sending an exhausted rider back out can turn a missed delivery promise into a safety problem. The pressure to act arrives before the picture is clear.

With DispatchDesk, the manager can begin with a simple question: “Orders are backing up. What should I do first?” If packed orders are waiting for riders, the assistant checks whether rider availability is the immediate constraint and shows the facts behind its explanation. It helps the manager work through a response that fits the store's rules, such as bringing in standby capacity when the riders on shift cannot safely take more work. The manager can review the reasoning and approve the next step with the relevant information in front of them.

“We built DispatchDesk for the moment when everyone is waiting for the manager to make a call,” says the DispatchDesk team. “The manager needs to understand what will help and why. Rider safety and the manager's authority are part of every recommendation.”

“When the queue builds up, I'm trying to work out what's wrong while everyone is asking what to do,” says Karthik Reddy, a dark store manager in Koramangala. “With DispatchDesk, I can see why a change is being suggested and check it before I give the instruction. I can explain the decision to my team.”

DispatchDesk is available through a guided browser demonstration using a simulated store. Managers can request access from the project team, work through an evening dispatch scenario, and see how the assistant supports their decisions. All dispatch changes in the demonstration are simulated.

## Public FAQs

### 1. I already have a dispatch dashboard. Why would I use DispatchDesk?

A dashboard helps you see the state of your store. DispatchDesk helps you work through a question about that state: why orders are waiting, which response fits the situation, and what you need to check before acting. You can inspect the evidence behind the answer and ask a follow-up in the same conversation. It is designed for the point where you need to turn what you see into a decision.

### 2. How can I check whether an answer is right?

DispatchDesk shows the operational records and policy passages used in its answer, including when the operational data was observed. You can inspect them in the evidence panel. Figures must come from those records or a calculation that can be checked against them. If the evidence supports only a possible explanation, the answer says so. A rain flag, for example, does not by itself prove why a delivery was late.

### 3. Can it help me understand why last night went badly?

Yes. Ask it to compare the affected period with the previous evening. It compares time spent picking and packing, waiting for a rider, and travelling to the customer, alongside order volume, rider availability, and weather information. The answer identifies the largest measured change and explains what it suggests you should investigate. In the demonstration, these comparisons use the supplied historical records.

### 4. What can I do when it rains and there are too few riders?

Ask DispatchDesk to work through the waiting orders with you. It checks which orders could share a rider under the batching policy and explains why others must travel separately. Frozen items are excluded from batches. If the evidence supports an ETA, it provides an estimated range you can review for a customer message. It will not turn a general rain guideline into a promised arrival time. Batches and messages require your approval.

### 5. Will it send instructions or change dispatch without asking me?

No. Operational changes are presented for your review. Approval applies to the specific proposal shown; changed conditions require the proposal to be checked again, and a material change requires fresh approval. This covers rider assignments, batches, incentives, service-area changes, standby call-ins, and communications. The demonstration records simulated outcomes and has no connection to real dispatch or messaging systems.

### 6. What happens if a recommendation would put a rider at risk?

DispatchDesk rejects requests to encourage speeding, break traffic rules, ride in unsafe conditions, penalize weather- or safety-related delays, or exceed working-hours and break limits. It checks recorded rider hours and breaks before proposing further work, explains the applicable rule, and offers permitted alternatives. Manager approval cannot override these restrictions. The demonstration uses the illustrative rules in the [sample playbook](initial/sample_data/dispatch_operations_playbook.pdf); a real deployment would need the operator's authoritative policy.

### 7. Will I have to explain my store's preferences every shift?

You can save preferences and use them in a later session for the same manager and store. These include your pending-orders-per-available-rider threshold, the hours when it applies, batching restrictions, and incentive cap. DispatchDesk confirms what it saved and raises conflicts before proposing an action. A preference can make a rule stricter, but cannot relax mandatory safety requirements. The first release checks thresholds during your interactions; it does not monitor the queue or send alerts in the background.

### 8. What if the information is missing or out of date?

DispatchDesk tells you what it cannot establish. It may show an older snapshot with its observation time, but will not present it as the current queue. It asks for clarification when a reference such as “that rider” is ambiguous. Missing operational inputs prevent an unsupported figure or ETA; missing policy evidence prevents a recommendation that depends on it. You can report a problem to the team hosting the demonstration.

### 9. What happens to the information I enter?

The demonstration uses synthetic store records and preferences. Your question and the supporting context needed for an answer are sent to Groq's hosted chat model. That context can include policy passages, operational records, and saved preferences. Evidence logs may also contain these inputs and results. Enter only synthetic information: the project has not established the permissions, retention arrangements, or access controls needed to process real customer or rider data.

### 10. How do I try it, and what does it cost?

Request a guided demonstration from the project team. You use a prepared store scenario in a browser and ask questions in English; you do not connect your own store systems. Commercial pricing, production onboarding, and ongoing support commitments have not been set. The initial release is a demonstration of the proposed experience.

## Internal FAQs

### 1. What is the central customer promise?

Help the manager reach a decision they can explain and defend when deliveries fall behind. The manager should understand the apparent bottleneck, see why a proposed response fits, and retain authority over the next action. The value we need to establish is whether this reduces the effort of gathering and interpreting evidence during a busy shift. A convincing explanation is useful only when the supporting facts and rules are correct.

### 2. What evidence do we have that this problem matters?

The [project requirements](initial/requirements.md) describe Karthik's store, evening peaks, rain disruption, and six situations requiring a decision. They supply the persona and operating assumptions; we have no documented customer interviews, observed shifts, or measured decision times. Karthik and the launch quotes illustrate the intended customer experience.

The next discovery step is to observe how managers diagnose a backlog today: which screens they consult, whom they contact, how they choose an intervention, and where they hesitate or make mistakes. That evidence should determine whether the problem justifies further investment.

### 3. Why choose conversation over improving the existing dashboard?

Conversation may help when a question crosses operational data, written policy, and the store's preferences. It lets a manager ask a follow-up without having to find and combine those sources again. A dashboard may be better for repeated monitoring, and a rules engine can calculate thresholds and check eligibility predictably. Those alternatives need to be taken seriously.

Our proposed design uses explicit calculations and constraint checks underneath the conversation. The model helps interpret the question and explain the result. We must test whether that interaction adds enough value to justify its latency, cost, and potential for error.

### 4. What are we building first, and what will disappoint users?

The first release covers one simulated store and the six required situations: a current backlog, comparison of two evenings, rain batching and ETA advice, unsafe rider pressure, saved preferences, and a rider nearing working-hours limits. It includes evidence inspection, approval of simulated actions, and memory across at least two sessions.

A manager cannot use it to change a real queue. There are no order-management, rider-app, maps, payment, or messaging integrations. The interface supports English, and continuous alerts are outside the initial scope. These limits mean the demonstration can test the interaction and technical behavior, while usefulness in a real shift remains an open question.

### 5. What must always be true about a recommendation?

Operational claims must be supported by retrieved records or a documented calculation from them. Policy and rider safety constrain the available choices. Store preferences must be recalled and conflicts surfaced. The manager must approve each operational change.

These requirements need enforcement in code and evaluation as well as model instructions. Before simulated execution, the system must check that the approved proposal still fits the current state. If a check fails, it must explain the problem and return to the manager.

### 6. How will we know whether managers find it useful?

In a subsequent supervised study, compare the assistant with direct access to the same operational information and policies. Use matched scenarios and an agreed rubric to measure whether managers reach the correct, compliant decision, how long it takes, and whether they can explain their reasoning. Observe whether they inspect the evidence and understand when the assistant is uncertain.

Teammates can help identify usability problems during development. Evidence of adoption or better decisions requires participation by actual managers. We have no measured improvement in decision time, delivery performance, or operating cost to claim today.

### 7. What must the demonstration prove?

The [six-pager](6-pager.md) proposes the following acceptance criteria. Results must include the dataset, corpus, model settings, and evidence for each case.

| Check | Required result |
| --- | --- |
| Retrieval | The rain-delay question returns both delay-diagnosis and rain-playbook guidance within its top three passages. |
| Required interactions | All six scenarios meet their expected behaviors, with individual results reported. |
| Grounding | Every operational claim is traceable to tool data or a documented calculation; zero unsupported claims in evaluated runs. |
| Safety and control | Zero prohibited proposals, unapproved actions, or silent preference overrides in evaluated runs. A benign question about riders due a break remains answerable. |
| Memory | Threshold, time window, batching restriction, and incentive cap are recalled and applied in a second session for the same manager and store. |
| Failure handling | Timeout, ambiguous reference, missing policy, and preference conflict each receive an appropriate fallback or clarification. |

Record tool failures, stale data, guardrail outcomes, and preference recall, with counts and denominators. Measure latency under stated conditions. Passing these cases supports acceptance of the demonstration; the customer study addresses the separate question of usefulness.

### 8. What are the main technical dependencies?

The [agreed stack](team.md) is Flask, Gradio, PostgreSQL with pgvector, Groq-hosted GPT-OSS 120B, and uv. The embedding model remains subject to retrieval evaluation. Policies supply written guidance; tools supply operational records; code performs calculations and checks; the model explains the result.

The [sample dataset](initial/sample_data/dispatch_dataset_sample.xlsx) and playbook need to support every required scenario. In particular, we must verify zone adjacency, detour information, rider timing, and defensible inputs for ETA estimation. Policy wording must become explicit prototype rules. A documented simulation clock and freshness limit must prevent old fixture data from appearing current simply because it was fetched again.

### 9. What exists today, and how will the team deliver the rest?

We are in Week 1. The repository contains the Flask hello-world route and test, package configuration, CI workflows, planning documents, and sample data. The dispatch experience remains to be built.

The [source plan](initial/tasks.md) targets retrieval and Gradio in Week 1, tools and memory in Week 2, guardrails and caching in Week 3, and evaluation, observability, and demonstration readiness in Week 4. Groups of two or three people will take tasks as work progresses, recording ownership, reviewers, and evidence in the [tracker](task-tracker.md). Team capacity and remaining effort must be confirmed before setting the demonstration date.

### 10. Who would pay for this, and what would it cost to operate?

The manager is the intended user. A store operator or regional operations team is a possible buyer, but purchasing authority and willingness to pay remain untested. Discovery must establish the cost of the current problem and the integration effort an operator would accept.

Prototype costs include model usage, any hosted application and database services, embedding computation, and engineering time. We need measured request volumes, token usage, retries, and infrastructure consumption before estimating cost per completed interaction. Pricing, a spending cap, and a commercial model remain open. The persona's order volume does not establish a market-size or revenue forecast.

### 11. What would make us reconsider the product?

An assistant that gives plausible but unsupported advice would be unsafe to rely on. A correct assistant that takes longer to use than the existing workflow may have little value. Incomplete operational data or policies could also prevent a useful answer when the manager needs one most.

Grounding, safety, or approval failures require correction and another recorded evaluation before technical acceptance. If managers gain little from the conversation, we should reconsider the interface or simplify the solution. Successful technical evaluation would justify considering a supervised customer study; real integrations would require a further decision based on that evidence.
