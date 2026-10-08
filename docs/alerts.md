# DispatchDesk alerts design

Status: implemented. Section 1 (setting alerts through chat) is in `backend/service/setting_changes.py`. Sections 4 to 6 (checking alerts, the pop-up, the `<alerts>` block) are in `backend/service/alerts.py`, `backend/queries/alerts.py` and `backend/ui/alerts.py`, with migration `0011_alert_events`. Differences from the draft below: `alert_events` is a log with one row per pop-up (no active/resolved status), which gives both the cooldown and the count per day; there is no demo-time override yet, so windows use the real time in Asia/Kolkata; and the pop-up's buttons are Diagnose, Start new chat and dismiss (no snooze).

Scope: MVP. It covers the five alert types in the preference catalogue ([memory.md](memory.md), section 2). The chat path in section 1 also works for the other configurable items (surge-only batching, incentive cap, briefing); cold-chain isolation is locked.

## Requirement

1. **Set through chat.** The manager can create, update, turn off or reset any of the five alerts in plain language, for example "on weekends after 7pm, alert me when pending orders per available rider goes above 2" (requirements.md, sample query 5).
2. **Stay within the defined range.** Every value is checked against the catalogue's limits. A value outside them is never saved; the assistant explains the limit.
3. **Pop up on breach.** When live data crosses a threshold the manager set, inside its window and outside its cooldown, the alert pops up on screen.

Alerts are per manager. A confirmed alert belongs to the manager who set it, not to a chat and not to the store as a whole. It applies in every conversation and session of that manager until they change it, and never to another manager.

## Principles

1. Code decides; the model proposes. The model turns a chat message into a structured proposal. Code validates it, the manager confirms it, and code saves it. Code computes every measure and decides whether an alert fires.
2. No silent saves. Every change made through chat is shown as a proposal and saved only after the manager confirms it.
3. One write path. Chat and the Settings tab both save through `PreferenceService.set()`, so validation, history and conflicts behave the same.
4. Updates change only what the manager mentioned. Every other field keeps its current value.
5. Only the catalogue. The manager can configure the five alerts below and nothing else. A request for any other alert is declined.
6. No data is not "all clear". If alerts cannot be checked, the panel says so.
7. Alert figures come from code. The assistant may explain an alert, but it quotes the measured value and "as of" time computed by code, never its own numbers.

## The five alerts

Seeded by migration `0006_preferences.py`. Each fires when its measure compares to the manager's threshold using the operator.

| Code | Name | Measure | Op | Default | Allowed | On by default | Cooldown |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `rider_shortage_alert` | Rider shortage | packed orders waiting for a rider ÷ available riders | gt | 2 | 0.5 to 2 (policy) | yes | 15 min |
| `orders_piling_up_alert` | Orders piling up | orders picking or packed and waiting | gte | 8 | 1 to 50 | yes | 15 min |
| `order_waiting_too_long_alert` | Order waiting too long | minutes the oldest packed order has waited | gt | 8 | 1 to 8 (policy) | yes | 10 min |
| `frozen_order_waiting_alert` | Frozen order waiting | minutes the oldest packed frozen order has waited | gt | 5 | 1 to 8 | no | 10 min |
| `sla_dip_alert` | SLA dip | 10-minute SLA % for the last completed hour | lt | 80 | 50 to 100 | no | 60 min |

Measure definitions:

- "Packed orders waiting" are orders with status `packed_waiting_rider`. "Available riders" are riders with status `available`.
- With zero available riders, rider shortage fires whenever any order is waiting.
- Order age is `as_of - placed_at`, measured at the snapshot's `as_of`.
- The operator is fixed per alert. The manager sets the threshold, not the comparison.

Options per alert, all optional: `days` (weekday names), `start` and `end` (HH:MM, Asia/Kolkata), `cooldown_min` (5 to 240). No window means at all times. A null `end` means end of day. A window may cross midnight.

## 1. Setting alerts through chat

### Flow

```
Manager message
  -> answer model calls propose_setting_change(...)
  -> code merges the proposal into the current effective setting
  -> code validates the merged setting (validate() from service/preferences.py)
       invalid -> the assistant explains the limit; nothing is shown to confirm
       valid   -> pending-change card: old -> new, [Confirm] [Cancel]
  -> Confirm -> re-validate against the current setting -> PreferenceService.set()
  -> Active settings, the Settings tab and the alert checker use the new value
```

### The tool

The answer model is given one tool. It is offered on every chat turn; the model calls it only when the manager's latest message asks to change an alert.

`propose_setting_change`:

| Field | Type | Notes |
| --- | --- | --- |
| `code` | one of the five alert codes | required |
| `action` | `set`, `turn_off`, `reset` | required |
| `value` | number | threshold; omit to keep the current one |
| `days` | list of weekday names | omit to keep the current days |
| `start`, `end` | HH:MM | omit to keep the current times |
| `clear_days` | boolean | true removes the days, so the alert applies every day |
| `clear_times` | boolean | true removes start and end, so the alert applies at any time of day |
| `cooldown_min` | integer | omit to keep the current cooldown |

- `set` turns the alert on, applying any given fields on top of the current setting.
- `turn_off` keeps the stored threshold and window but disables the alert.
- `reset` returns the alert to its catalogue default (marks the active row `removed`).
- One message can produce several calls, for example two alerts at once. Each becomes its own card and is validated on its own.

The tool returns the validation result to the model as JSON, either `proposed` with the old and new settings or `rejected` with the reason, always with `saved: false`, so the reply matches what the card shows. If the model's reply is empty or only echoes that JSON, code writes the reply from the proposals (`tidy_reply`).

### Merging

`PreferenceService.set()` stores exactly what it is given. Chat must not drop fields the manager did not mention, so the chat path:

1. Loads the current effective setting (the manager's active row, or the default).
2. Applies only the fields present in the proposal.
3. Validates and shows the merged result.

Example: the current setting is "above 2, Sat and Sun from 19:00". "Change the shortage alert to 1.5" becomes "above 1.5, Sat and Sun from 19:00", not "above 1.5, at all times".

### Examples

| Manager says | Result |
| --- | --- |
| "On weekends after 7pm, alert me when pending orders per available rider goes above 2" | Card: Rider shortage, on, above 2, Sat and Sun from 19:00 |
| "Change the rider shortage alert to 1.5" | Card: above **2 → 1.5**, window unchanged |
| "Make it apply every day" | Card: window **Sat and Sun from 19:00 → every day from 19:00** |
| "Turn on the frozen order alert at 4 minutes" | Card: Frozen order waiting **off → on, above 4 min** |
| "Turn off the SLA alert" | Card: SLA dip **on → off** |
| "Reset the shortage alert" | Card: Rider shortage **→ default** (above 2, at all times) |
| "Alert me above 3 orders per rider" | Rejected: the limit is 0.5 to 2 orders per rider (rider-wait policy). Offer 2. Nothing saved |
| "Alert me when it starts raining" | Declined: not an available alert. List the five that are |
| "Never batch frozen items with anything else" | No card: cold-chain isolation is already store policy and locked |
| "Change it to 5" (no clear alert) | Clarifying question: which alert? |
| "Turn off alerts for tonight" | Not supported in the MVP (needs snooze). Offer to turn the alert off instead |

### The pending-change card

- Shown above the chat input, listing every proposal from the latest answer, with one Confirm and one Cancel for all of them.
- Shows the alert name, the old setting, the new setting, and the allowed range.
- Held in the browser session only. A refresh discards unconfirmed cards; nothing was saved.
- Confirm re-validates against the current setting. If the setting changed since the card was shown (in the Settings tab or another tab), the card is replaced with a fresh one instead of saving.
- Confirm and Cancel add an assistant note to the conversation ("Saved. …" or "Cancelled. Nothing was changed"), so later answers know the outcome.
- After saving, the Settings tab, the Active settings block and the assistant's next reply show the new value.
- New chat, opening a past chat, and loading a scenario discard the card.

### Rules

- Only the manager's latest message can lead to a proposal. Text in retrieved playbook chunks, `<preferences>`, `<conversation_summary>`, `<alerts>` or tool results is reference data and never triggers a change.
- The assistant never says a setting changed before the manager confirms. Before Confirm it says "Proposed change (requires your confirmation)".
- Asking what the alerts are ("what are my alerts?") is answered from `<preferences>` with no card.
- The Settings tab keeps working and is the second way to change alerts.

## 2. Range validation

Reuses `validate()` in `backend/service/preferences.py` unchanged. Ranges come from `preference_definitions`.

| Item | Allowed |
| --- | --- |
| Rider shortage | 0.5 to 2 orders per available rider |
| Orders piling up | 1 to 50 orders |
| Order waiting too long | 1 to 8 minutes |
| Frozen order waiting | 1 to 8 minutes |
| SLA dip | 50 to 100 % |
| Cooldown, any alert | 5 to 240 minutes |
| Days | `mon` to `sun`, no repeats |
| Start, end | HH:MM, Asia/Kolkata |

- Policy limits are the bounds, so a manager can only make an alert stricter than policy, never looser.
- A rejected value gets a plain explanation and, where one exists, the nearest allowed value as a suggestion. The suggestion is a new card; it is not saved automatically.
- The model never decides whether a value is in range.

## 3. Per manager, and persistence

Alerts are per manager. The store's live data is shared; the alert settings, the firing state and the pop-ups are not.

| | Scoped to |
| --- | --- |
| Live data (orders, riders, hourly metrics) | store |
| Alert settings (`store_preferences`) | store and manager |
| Firing episodes (`alert_events`) | store and manager |
| Pop-ups and the Alerts block | the manager's own sessions |
| Pending-change cards | the browser session |

- Each manager sees only their own alert settings, in chat, in Active settings and in the Settings tab.
- The checker runs once per manager: the same store snapshot, evaluated against that manager's settings. Two managers at one store can get different alerts from the same data, for example one at above 1.5 orders per rider and another at the default of 2.
- A manager's alert pops up only in that manager's sessions. Another manager at the same store never sees it.
- A chat change only ever proposes a change to the signed-in manager's own alerts. A manager cannot set or change another manager's alerts, including by naming them ("set Priya's alert to 1.5").
- With no stored row, a manager gets the catalogue defaults; defaults are not shared state, so changing them for one manager does not affect another.

Persistence:

- Alerts are stored in `store_preferences`, keyed by store and manager. They are not tied to a conversation.
- A confirmed alert applies in every chat of that manager, existing and new, after New chat, after a scenario load, and across refreshes, restarts and sessions.
- Each change inserts a new active row and marks the previous one `superseded`. Reset marks it `removed`. History is kept.
- Chat changes record their source: add `source` (`settings_tab` or `chat`) and `source_message` (conversation id and message position) to `store_preferences`. These were deferred columns in memory.md.

## 4. Evaluation

### The checker

A pure function in `backend/service/alerts.py`:

```python
evaluate_alerts(snapshot, settings, now) -> list[AlertResult]
```

- `snapshot`: the loaded scenario's orders, riders and hourly metrics, with `as_of`. Built by `live_snapshot()` in `backend/queries/alerts.py`. This is the same read the `get_live_dispatch_status` tool (tasks.md task 13) needs, so the tool reuses it.
- `settings`: the manager's effective alert settings.
- `now`: the time used for windows (below).
- `AlertResult`: code, name, measured value, threshold, operator, `as_of`, the window that applied, and whether it is breached.

The checker never calls the model.

### When it runs

- Every 30 seconds while the page is open (`gr.Timer`).
- After every chat answer.
- After a scenario load and after any alert setting is saved.

### Clock

The snapshot is static, so two times are kept apart:

- **Measures** use the snapshot's `as_of`. Order ages are fixed at load time and do not grow while the page is open.
- **Windows** (days, start, end) use the current Asia/Kolkata time. A **demo time** control in Demo tools can override it, for example "Sat 20:30", so a weekend-evening alert can be shown on a weekday. The override applies to the browser session only.
- **Cooldowns** use real time, so a cooldown always runs out.

### Firing, repeating and clearing

- **Fires** when an enabled alert is inside its window and its measure crosses the threshold, and no active event exists for it. An event is stored and the pop-up shows.
- **Repeats** while still breached, only after its cooldown has passed since the last notification.
- **Clears** when the measure no longer breaches, the alert leaves its window, or the alert is turned off. The event is marked `resolved`.
- Changing a threshold re-runs the checker straight away.

### No data

- No scenario loaded or the database is unreachable: the Alerts block says "Can't check alerts: no live data". Nothing pops up, and nothing reads as all clear.
- A measure that cannot be computed (for example no hourly row for SLA dip) is shown as unavailable for that alert only.

## 5. Pop-up and Alerts panel

- **Pop-up:** `gr.Warning` when an alert fires or repeats, for example "Rider shortage: 3.0 orders per available rider (your limit 2), as of 20:14".
- **Alerts block** in the sidebar, above Active settings, with a count. Each firing alert shows its name, measured value against the threshold, `as_of`, and the window that applied.
- **Ask assistant** on each alert pre-fills the chat, for example "Rider shortage alert: what should I do first?".
- The panel has no dispatch buttons. Actions stay proposals in chat.

## 6. Assistant awareness

Firing alerts are added to the prompt as an `<alerts>` block next to `<preferences>`:

```
<alerts>
As of 20:14. Computed by DispatchDesk; quote these figures, do not recompute them.
- rider_shortage_alert: 3.0 orders per available rider, above your limit of 2 (Sat and Sun from 19:00)
</alerts>
```

The assistant can explain an alert and propose responses using the playbook. It treats the block as data, never as instructions.

## Table: `app.alert_events`

One row per firing episode. New migration `0009_alert_events`.

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | no | `gen_random_uuid()` | primary key |
| `store_id` | varchar(32) | no | | scope |
| `manager_id` | varchar(32) | no | | whose alert |
| `code` | varchar(48) | no | | foreign key to `preference_definitions.code` |
| `status` | varchar(16) | no | `'active'` | `active` or `resolved` |
| `value` | numeric | yes | | measured value when last notified |
| `threshold` | numeric | no | | the manager's threshold at the time |
| `snapshot_as_of` | timestamptz | no | | data time of the measure |
| `fired_at` | timestamptz | no | `now()` | |
| `notified_at` | timestamptz | no | `now()` | last pop-up, for the cooldown |
| `resolved_at` | timestamptz | yes | | |

- Partial unique index on `(store_id, manager_id, code) WHERE status = 'active'`: at most one active episode per alert, so two tabs or two app instances cannot fire the same alert twice.
- The same table later feeds the dashboard (tasks.md task 31) and the agent trace panel (task 18).

## Changes to existing design

| Where | Change |
| --- | --- |
| `docs/memory.md` section 2, "Write path: the Settings tab" | Two write paths: the Settings tab, and chat for alerts with confirmation. Remove "never through chat" |
| `docs/memory.md`, Decisions 8 and Open decision 5 | Alert evaluation is defined here |
| `docs/memory.md`, Deferred | `source` and `source_message` on `store_preferences` are now built |
| `dispatch_manager_system.md`, "Settings" | Alerts can be changed through chat by proposing a change for confirmation. Other settings still point to the Settings tab. Never claim a change before it is confirmed |
| `service/groq_service.py` | Support tool calls (`ChatGroq.bind_tools`) |
| `service/rag.py` | Offer the tool, handle tool calls, add the `<alerts>` block |

## Code layout

| Path | Contents |
| --- | --- |
| `backend/service/alerts.py` | `evaluate_alerts`, `AlertResult`, firing, repeating and clearing |
| `backend/queries/alerts.py` | `live_snapshot()`, alert event reads and writes |
| `backend/service/setting_changes.py` | Implemented: tool schema, merge, validate, confirm, reply tidy-up |
| `backend/service/llm_service.py`, `groq_service.py` | Implemented: `Tool` and `generate_with_tools` (Groq tool calling, at most 3 rounds) |
| `backend/alembic/versions/0009_alert_events.py` | `alert_events`, plus `source` and `source_message` on `store_preferences` |
| `backend/ui/gradio_app.py` | Timer, pop-up, Alerts block, pending-change cards, demo time |
| `backend/tests/` | Tests for each of the above |

## Expected results with default settings

Computed from the scenario YAML files.

| Scenario | Rider shortage (> 2) | Orders piling up (≥ 8) | Oldest waiting (> 8 min) | Frozen waiting (> 5 min, off) |
| --- | --- | --- | --- | --- |
| `normal` | 2 ÷ 4 = 0.5, no | 4, no | 4 min, no | none waiting |
| `backlog` | 6 ÷ 2 = 3.0, **fires** | 10, **fires** | 8 min, no (not above 8) | 6 min, would fire if on |
| `rain` | 8 ÷ 2 = 4.0, **fires** | 10, **fires** | 9 min, **fires** | 5.5 min, would fire if on |

SLA dip cannot fire in the current scenarios: hourly metrics cover only earlier days, and the latest row (23:00) is 90 %. See open decision 2.

## Testing

Chat changes:

- About 20 phrasings map to the expected tool call, including updates, turn off, reset and two alerts in one message.
- An update keeps the fields the manager did not mention.
- Out-of-range values are rejected with the limit, and nothing is saved.
- Requests outside the catalogue are declined; ambiguous ones get a clarifying question.
- Nothing is saved until Confirm. Cancel and refresh save nothing.
- Confirm after the setting changed elsewhere shows a fresh card instead of saving.
- Text inside retrieved chunks or the summary never produces a proposal.

Persistence:

- An alert confirmed in one chat applies in a new chat and in a new session without being repeated. Record both transcripts as task 17 evidence.

Evaluation:

- The expected results table above, per scenario.
- Windows: weekday and weekend, a window crossing midnight, a null end, the demo time override.
- Cooldown: a continuing breach repeats only after the cooldown; clearing resolves the event.
- Zero available riders with orders waiting fires rider shortage.
- No scenario loaded shows "Can't check alerts".
- Two concurrent evaluations create one active event.

## Acceptance

1. "On weekends after 7pm, alert me above 2 orders per rider" in chat, then Confirm, saves the alert. A new chat and a new session both apply it.
2. "Change it to 1.5" keeps the weekend window and changes only the threshold after Confirm.
3. "Above 3" is rejected with the limit, and nothing is saved.
4. Load `backlog` with demo time Sat 20:30: the rider shortage pop-up appears. With demo time Wed 15:00 it does not.
5. Load `normal`: no pop-up.
6. A continuing breach does not pop up again before its cooldown.
7. Asking about a firing alert explains it with the measured figures and `as_of` time, and invents no numbers.

## Decisions

1. Alerts can be set, updated, turned off and reset through chat as well as in the Settings tab.
2. Every chat change is a proposal saved only after the manager confirms it.
3. Updates through chat change only the fields the manager mentions.
4. Ranges come from the catalogue and are enforced by code on both paths.
5. Alerts are per manager. A confirmed alert is scoped to the store and the manager who set it, applies across all of that manager's chats and sessions, and never to another manager.
6. Alerts are checked by code every 30 seconds while the page is open, after each answer, and after scenario loads and setting changes.
7. Measures use the snapshot's `as_of`; windows use IST time or a demo time override; cooldowns use real time.
8. Fired alerts are stored in `alert_events`, with at most one active episode per alert.

## Open decisions

1. Done: the chat path covers every configurable catalogue item.
2. SLA dip: define "last completed hour" against the snapshot, and add a same-day hourly row to a scenario so it can fire, or leave it unfireable in the demo.
3. Stale data: should a snapshot older than some limit be labelled stale, and what limit?
4. Snooze and acknowledge: add to the panel now, or keep with "prospective memory" for later?
5. Should the pop-up repeat in every open browser tab of the same manager, or only once?
6. Manager identity. Partly resolved: the store now has three shift managers (`app.managers`, migration 0010) and a picker in the sidebar, so each manager's alerts are separate. There is still no sign-in, so anyone using the demo can pick any manager.
