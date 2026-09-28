# Customer communication and honest delivery estimates

**Document ID:** DD-COMMS-001  
**Version:** 1.0 (2026-09-28)  
**Authority:** Simulated demo guidance based on the no-fabricated-ETA and explicit-approval requirements in `docs/initial/requirements.md` and `docs/6-pager.md`.  
**Applies to:** Delay notices, delivery estimates, and customer message drafts.

## Honest estimates

- Do not invent a delivery time, queue age, SLA, or ETA. Operational figures must be returned by an appropriate tool or calculated by approved deterministic logic from tool data.
- When an ETA can be supported, express it as a **range**, label it **estimate**, identify the relevant “as of” time/data basis where appropriate, and make clear it is not a promise.
- Never convert the ten-minute service promise or a stage target into a specific order's ETA.
- If no supported ETA is available, say that a reliable estimate cannot be provided from the available data. Acknowledge delay uncertainty without adding an unsupported number.
- Do not claim that a batch, radius change, call-in, or incentive will achieve a particular time or SLA result.

## Proactive delay notice

The project playbook calls for a proactive delay notice when an order passes 8 minutes in queue. The exact equality boundary is unresolved; for the demo, prepare the notice at 480 seconds or later as a conservative default and record that this is pending team confirmation. Use the order age from the live tool. The notice is a draft only and requires manager approval before sending.

## Draft-only customer contact

The assistant may draft a concise, respectful notice that acknowledges a delay and provides only a supported estimate, if one exists. It must not send the message. It must not blame or pressure a rider, disclose internal personnel details, or promise a recovery time not backed by data. If the manager requests a customer message, provide it as an unsent draft and explicitly await approval.

## Safe wording patterns

- **Supported range available:** “Your delivery is delayed. Current estimate: [tool-derived range]. This is an estimate, not a promise.” Replace bracketed values only with approved calculation outputs.
- **No supported range:** “Your delivery is delayed. We are checking the latest dispatch information and will share an updated estimate when available.”
- **Data stale or unavailable:** Do not present a stale estimate as current. Say the latest dispatch information is unavailable or timestamped and may be out of date; request manager review before any notice is sent.

These are templates, not authorization to message customers. Every communication remains a draft pending explicit manager approval.
