# DispatchDesk PR/FAQ

## Press release

### DispatchDesk launches an AI assistant to help dark store managers run operations more efficiently

**Store data, operating policies, and manager preferences come together in one conversation to help managers set priorities, assess options, and make informed decisions.**

**BENGALURU:** DispatchDesk today launched an AI assistant for dark store managers who need to turn operational information into decisions throughout their shift. Managers can ask what needs attention, how to use available capacity, or what changed in their store's performance. DispatchDesk connects the order queue, rider availability, delivery-stage history, and operating policies to explain the situation and recommend practical next steps.

For Karthik Reddy, a dark store manager in Koramangala, running the store means making a sequence of connected decisions: which orders to prioritize, whether available riders can handle the queue, when to request support, and what to tell customers. The information he needs is spread across dispatch screens, hourly reports, and playbooks. Each decision takes time to assemble and check, especially when several parts of the operation need attention at once.

With DispatchDesk, Karthik can start with, “What needs my attention in the store, and what should I do next?” The assistant checks the queue and available capacity, identifies where work is accumulating, and explains which priorities follow from the data. If packed orders are waiting, it can help him assess returning riders, eligible batches, or standby capacity. If the figures point to longer packing times, it directs his attention to that stage. Each recommendation includes the relevant facts and policies so Karthik can judge the action before approving it.

The same conversation supports decisions beyond the immediate queue. Karthik can compare two evenings, see how packing, rider wait, and travel time changed alongside order volume and weather, and use those findings to decide what needs a closer look next shift. DispatchDesk remembers his alert thresholds, batching preferences, and incentive caps, bringing that context into later conversations. Operating rules, including cold-chain requirements and working-hours limits, are applied as part of assessing every option.

“I need to know where my attention will make a difference,” says Karthik. “Having the store's numbers and rules together helps me work through the options and explain why I'm making a particular call.”

“We built DispatchDesk to help managers spend less time piecing information together and more time managing the shift,” says the DispatchDesk team. “The assistant connects what is happening, what the policies allow, and what the manager wants to achieve. The manager stays in control of the decision.”

Explore [DispatchDesk](https://supply-chain-bv-production.up.railway.app) with prepared store scenarios, inspect the records, and ask an operational question. The demonstration uses synthetic data and simulates approved changes.

## Customer FAQs

### 1. How does DispatchDesk help me manage my store day to day?

It helps you understand the state of your operation, decide where to focus, and work through the next action. Ask which work needs attention, whether current rider capacity matches the queue, or how performance compares with a previous shift. DispatchDesk brings the relevant records and policies into the answer and prioritizes options with reasons. Its initial scope covers order flow, dispatch capacity, delivery performance, and customer communication.

### 2. What makes its recommendations grounded in my store?

Recommendations use the relevant operational facts, applicable policies, and any saved manager preferences. For example, proposing a batch requires checking the actual orders and rider availability against geography, item, detour, and cold-chain rules. The answer shows the supporting figures, their observation time, and the relevant policy. It explains excluded options and separates what the records show from what it recommends you do. General policy questions can be answered from the playbook alone.

### 3. How can it help me decide whether to use existing capacity or request more support?

It looks at the work waiting, the age and attributes of orders, and riders who are available, returning, on break, or on standby. It then assesses the options permitted by the playbook, such as prioritizing eligible orders, batching compatible deliveries, requesting standby capacity, or adjusting serviceability. Customer updates can be prepared alongside the proposal. Missing availability, route information, or an incentive cap is identified before an option depending on it can be recommended.

### 4. Can I use it to improve how I run the next shift?

You can compare equivalent periods across days and see changes in order volume, riders online, packing time, rider wait, and travel time. DispatchDesk connects those observations to the operating guidance so you can identify where to investigate and what adjustments to consider. It distinguishes an observed change from a possible explanation. Any delivery estimate must come from operational data, be labeled as an estimate range, and carry its observation time.

### 5. Will it remember how I want the store to operate?

You can save alert thresholds and their applicable time windows, batching restrictions, and an incentive cap for your store. DispatchDesk confirms these preferences and applies them when the same manager returns in a later session. Conflicts are surfaced for you to resolve. Preferences can impose stricter limits, but cannot relax mandatory policy. Thresholds are evaluated during your interactions; continuous background monitoring is outside the core scope.

### 6. Who makes the final decision and carries out the action?

You do. DispatchDesk prepares assignments, batches, incentives, service-area changes, call-ins, and messages as proposals. It explains the basis for each so you can approve, reject, or ask for another option. Execution is a separate step requiring explicit approval and a check that the proposal still fits the current state. In the demonstration, approved changes affect only synthetic data and never contact real riders or customers.

## Internal and guardrail FAQs

### 7. How do we keep efficiency recommendations consistent with store policy, rider safety, and working hours?

Recommendations must satisfy the relevant dispatch, batching, cold-chain, customer-communication, and rider policies together. Frozen-item orders remain single-drop; rider assignments require verified hours and break status. DispatchDesk must decline requests to speed, break traffic rules, skip mandatory breaks, exceed shift limits, or impose pay penalties for weather- or safety-related delays. It cites the applicable rule and offers permitted alternatives. Manager approval cannot waive these limits. The demonstration uses illustrative operating policies.

### 8. What should the assistant do when the evidence is incomplete?

State what is known, identify what is missing, and explain the next useful check. Unavailable or stale data must never become invented queue counts, rider states, timings, or ETAs. Older records carry their observation time. Ambiguous references need clarification when the available records cannot resolve them. Policy guidance can still help the manager work through a question, while specific recommendations wait for the facts they require.

### 9. How will we know DispatchDesk helps managers make better decisions?

Evaluate whether managers can identify the operational issue, assess the relevant options, and choose a policy-compliant action using evidence they can inspect. The six cases in the [requirements](initial/requirements.md) cover current priorities, performance comparison, capacity and batching, policy conflicts, preference recall, and working-hours decisions. Check correct figures and citations, explained exclusions, recall across two sessions, useful handling of unavailable data, and explicit approval before any action.

To assess efficiency, compare the time and completeness of a decision with and without the assistant using the same records and policies. Record the results before claiming time savings or better delivery performance. The current prototype provides policy chat and separate scenario inspection; connected operational tools, persistent preferences, and simulated approval and execution are the next capabilities needed to evaluate the full experience.
