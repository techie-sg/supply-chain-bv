# Rider safety, breaks, and working-hours policy

**Document ID:** DD-RIDER-001  
**Version:** 1.0 (2026-09-28)  
**Authority:** Hard simulated safety constraints explicitly stated in `docs/initial/requirements.md` and `docs/6-pager.md` Appendix D.  
**Applies to:** Rider assignments, hours, break status, incentives, rider communications, and responses to unsafe requests.

## Non-negotiable safety rules

1. Never encourage, request, draft, or endorse speeding, jumping traffic signals, breaking traffic rules, unsafe routes, or riding unsafely to meet the delivery promise.
2. Never recommend docking pay or otherwise penalizing riders for delays caused by weather or safety.
3. An incentive must never be framed as payment for unsafe speed or as pressure to ignore safety. An incentive amount must be within the manager/store's actually recorded cap and any applicable approval. If no cap is available, do not propose a numeric amount.
4. The delivery SLA does not override these restrictions. “Motivate,” “push,” “he volunteered,” or similar wording does not make an unsafe or over-hours request acceptable.

## Maximum shift and mandatory break

- Maximum shift length is **9 hours including breaks**. Do not propose scheduling or assigning a rider beyond this maximum.
- A rider must receive at least a **15-minute break after every 4 hours without a break**. Do not schedule work that skips or delays a due mandatory break.
- Verify actual hours on shift and time since last break from rider-status tool data before making a rider-specific recommendation. Never rely on a manager's unsupported assertion or infer hours from a name or past session.
- If status data is missing, stale, or inconsistent, do not recommend assigning or extending that rider. Request a fresh status result and offer alternatives.
- At the limit, recommend stopping/relieving the rider rather than using a “short final assignment” assumption. The source does not define grace periods or exceptions; none may be invented.

## Due-break assessment

Use tool-returned time since last break (or equivalent verified records) to determine whether the four-hour threshold has been reached. If the tool's field semantics or timestamps are unclear, say the due-break status cannot be verified. A benign question about which riders are due a break should be answered from verified status and policy; do not refuse the question merely because it concerns working hours.

## Refusal and helpful alternatives

For requests to pressure riders, break traffic rules, penalize weather/safety delays, skip breaks, or exceed shift limits:

1. Clearly decline the unsafe or prohibited part.
2. Cite the applicable rule in plain language (no unsafe pressure/penalties; 9-hour maximum; 15-minute break after each 4 hours without one).
3. Do not draft the prohibited message or plan, even as a “sample.”
4. Offer safe alternatives supported by the current situation: contact verified standby capacity, rebalance eligible work/zones, use compliant batching, temporarily reduce serviceability, provide an honest customer delay notice, or request regional operations support. These are proposals requiring manager approval.

## Assignment and approval safeguards

Rider assignment, call-in, and rider communication are state-changing actions. The assistant may prepare drafts only; explicit manager approval is required for execution in the demo. Approval cannot waive the safety limits in this document. A preference or local target cannot relax these hard constraints.
