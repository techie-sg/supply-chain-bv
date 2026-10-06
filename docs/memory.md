# DispatchDesk memory design

Status: section 1 (conversation history, with the chat sidebar) is implemented. The other sections are a draft for review and are not implemented yet.

Scope: MVP only. Tables keep the fewest columns that make the behavior work. Columns deferred on purpose are listed under "Deferred".

Memory lets the assistant remember a store's operating knowledge across sessions and shifts. It has these parts, built in this order:

| Order | Memory | Type | Purpose |
| --- | --- | --- | --- |
| 1 | Conversation history | Episodic | Every question and answer stored; the foundation for the rest |
| 2 | Preferences | Preference | Each manager's settings for a fixed catalogue of alerts, batching rules, incentive cap and greeting |
| 3 | Handover notes | Episodic | Short free-text notes passed between shifts |
| 4 | Conversation summary | Compressed | Columns on `conversations` that keep long chats within the model's context |
| 5 | Dreaming (suggestions) | Derived | A background review of past conversations that proposes preference changes and insights |
| Parked | Resolution notes | Episodic | Saved diagnoses (root cause, steps, outcome) for reuse |

Also planned but not designed yet: approval log, prospective memory (snoozed alerts, reminders), and trace events linked to messages (via message position). How alerts are evaluated, and any table for fired alerts, is pending a team discussion; this document only defines and stores them.

Requirement covered: preferences stated in one session are recalled, unprompted, in a later session with the same manager (requirements.md section 4 and sample query 5).

## Tables at a glance

Five new tables in the `app` schema for the MVP, plus one parked. Messages are a JSON list inside `conversations`, and the conversation summary is two columns on it. Details are in the numbered sections below.

| # | Table | Purpose | Fields |
| --- | --- | --- | --- |
| 1 | `conversations` | One row per chat. Holds its messages as a JSON list and its summary. | `id`, `store_id`, `manager_id`, `messages`, `summary`, `summary_covers_to`, `created_at`, `updated_at` |
| 2 | `preference_definitions` | Catalogue of every configurable item, with defaults and limits. Seeded by migration. | `code`, `category`, `name`, `description`, `value_type`, `unit`, `operator`, `default_value`, `min_value`, `max_value`, `allowed_values`, `default_enabled`, `default_cooldown_min`, `locked` |
| 3 | `store_preferences` | A manager's values for catalogue items. No row means the defaults apply. | `id`, `store_id`, `manager_id`, `code`, `enabled`, `value`, `options`, `status`, `created_at` |
| 4 | `handover_notes` | Free-text notes passed from one shift to the next. | `id`, `store_id`, `manager_id`, `shift`, `note`, `created_at` |
| 5 | `suggestions` | Proposals from the background review (dreaming) for the manager to accept or dismiss. | `id`, `store_id`, `manager_id`, `kind`, `payload`, `reason`, `evidence`, `status`, `created_at` |
| Parked | `resolution_notes` | Saved diagnoses (root cause, steps, outcome) for reuse. | `id`, `store_id`, `title`, `situation`, `root_cause`, `actions_taken`, `outcome`, `embedding`, `created_at` |

## Principles

1. Preferences come from a fixed catalogue with typed values and limits, not free text, so code can validate and check them.
2. Preferences are always loaded in full. They are few, so no vector search.
3. Preference rows are never edited in place. A change supersedes the old row, which keeps history.
4. A preference can make policy stricter, never looser.
5. Memory text is user-written data. The assistant treats it as reference, never as instructions.
6. Everything is scoped to a store.
7. Raw transcripts are not memory. The assistant recalls preferences and the last handover, not old chats.
8. The background review proposes; it never applies. Only the manager turns a suggestion into a preference.
9. A manager can only configure items in the catalogue. The assistant never accepts an alert or greeting view it cannot compute from real data.

## 1. Conversation history

Every manager question and assistant answer is stored in PostgreSQL. A **conversation** is one chat. Its messages are kept as a JSON list on the conversation row, and it has at most one summary (section 5).

Before this, history existed only in the Gradio session and was lost on refresh, restart, clearing the chat, or loading a scenario.

Other pieces depend on it: preference extraction, the summary, the trace, the approval log, Week 4 evals, and demo transcripts.

### Message shape

Each message records who said it, what was said, and when:

```json
{"who": "manager", "what": "Orders are backing up, what first?", "when": "2026-10-06T19:42:10+05:30"}
{"who": "assistant", "what": "Check the oldest packed order...", "when": "2026-10-06T19:42:14+05:30"}
```

- `who` is `manager` or `assistant`.
- `when` is an ISO 8601 timestamp in Asia/Kolkata.
- A message's position in the list is its identity. Messages are only appended, never edited, removed or reordered.

### Table

`app.conversations`, one row per chat:

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | no | `gen_random_uuid()` | primary key |
| `store_id` | varchar(32) | no | | scope |
| `manager_id` | varchar(32) | no | | the demo manager |
| `messages` | jsonb | no | `[]` | list of messages; must be a JSON array |
| `summary` | text | yes | | see section 5 |
| `summary_covers_to` | int | yes | | position of the last message included in the summary |
| `created_at` | timestamptz | no | `now()` | when the chat started |
| `updated_at` | timestamptz | no | `now()` | changes on every new message |

Index on `(store_id, manager_id, updated_at)` to find the latest chat.

### When a conversation starts

A new conversation starts when:
- No conversation exists yet for the store and manager.
- The manager clicks **New chat** in the sidebar.
- A scenario is loaded.

A browser refresh or app restart does not start one; the latest conversation resumes.

There is no idle timeout and no status column. The current conversation is the one most recently updated for the store and manager. A finished chat's end time is its `updated_at`.

If the same manager has two browser tabs open, both write to the latest conversation and their messages interleave. This is accepted for the demo.

### Write path

1. If there is no current conversation, create one.
2. Append the manager's message before calling the model, so a provider failure never loses the question. The append is one atomic `UPDATE` that also sets `updated_at`.
3. Call the assistant.
4. On success, append the assistant's message the same way.
5. On failure, append nothing and log the error. The chat then has a manager message with no reply. Never store stack traces or raw provider responses.

### Read path

- On page load, restore the latest conversation for the store and manager and show its messages. A refresh or app restart resumes the chat.
- Model history comes from the stored messages, replacing the Gradio state as the source of truth: the summary if one exists, followed by the messages after `summary_covers_to`, in order. `manager` maps to the model's user role.
- Pairs for evals and review are each manager message with the assistant message that follows it.

### Chat sidebar

A sidebar, like Claude's, lets the manager browse and continue past chats:

- **New chat** at the top starts a new conversation.
- **Recent** lists past conversations, newest first, titled by their first question. Empty conversations are not listed. The open conversation is highlighted.
- Clicking a chat shows its messages and makes it current by setting its `updated_at` to now. The next question continues that chat. No other state is needed, because the current conversation is always the most recently updated one.
- The list refreshes after each answer, New chat, opening a chat, and loading a scenario.
- The sidebar can be collapsed. On phones it starts closed and closes after a chat is chosen.

### Privacy and retention

- Stored only in the project's own PostgreSQL database.
- Messages may contain rider names and operational details, so retention must match the PR/FAQ data-handling claims.
- Deleting removes the whole conversation. No automatic expiry for now.
- Never store API keys.

## 2. Preferences

Every configurable item is predefined in a catalogue, `preference_definitions`. A manager's own values live in `store_preferences`. Alerts, batching rules, the incentive cap and the greeting all use this same pair of tables.

### Enums

Codes are stored as plain strings, and the catalogue table is the source of truth in the database. Python `StrEnum` classes in `backend/domain/memory.py` mirror them. Postgres `ENUM` types are not used because they are awkward to change in migrations.

| Enum | Values |
| --- | --- |
| `PreferenceCategory` | `alert`, `batching`, `incentive`, `briefing` |
| `PreferenceCode` | `rider_shortage_alert`, `orders_piling_up_alert`, `order_waiting_too_long_alert`, `frozen_order_waiting_alert`, `sla_dip_alert`, `cold_chain_isolation`, `surge_only_batching`, `incentive_cap`, `briefing` |
| `ValueType` | `number`, `boolean`, `choice`, `view_list` |
| `Unit` | `orders_per_rider`, `orders`, `minutes`, `percent`, `inr` |
| `AlertOperator` | `gt`, `gte`, `lt` |
| `BriefingView` | `rider_stats`, `order_queue`, `oldest_order_age`, `last_handover_note` |
| `Weekday` | `mon`, `tue`, `wed`, `thu`, `fri`, `sat`, `sun` |
| `PreferenceStatus` | `active`, `superseded`, `removed` |

### Table: `app.preference_definitions`

The catalogue. Seeded by the migration; managers never edit it.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `code` | varchar(48) | no | primary key; a `PreferenceCode` |
| `category` | varchar(16) | no | a `PreferenceCategory` |
| `name` | text | no | short label shown to the manager |
| `description` | text | no | what it does, given to the model |
| `value_type` | varchar(16) | no | a `ValueType` |
| `unit` | varchar(24) | yes | a `Unit`, for numbers |
| `operator` | varchar(4) | yes | alerts only; an `AlertOperator` |
| `default_value` | jsonb | yes | null means unset |
| `min_value`, `max_value` | numeric | yes | bounds for numbers; the policy limit where one exists |
| `allowed_values` | text[] | yes | options for `choice` and `view_list` |
| `default_enabled` | boolean | no | |
| `default_cooldown_min` | int | yes | alerts only |
| `locked` | boolean | no | a policy rule shown for visibility that managers cannot change |

Checks: `min_value <= max_value`; `category`, `value_type`, `unit` and `operator` take enum values only.

### The catalogue

**Alerts.** Each fires when its measure compares to the manager's threshold using the operator.

| Code | Name | Measure | Op | Unit | Default | Min | Max | On by default | Cooldown |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `rider_shortage_alert` | Rider shortage | packed orders waiting for a rider ÷ available riders | gt | orders_per_rider | 2 | 0.5 | 2 (policy) | yes | 15 |
| `orders_piling_up_alert` | Orders piling up | orders not yet out for delivery (picking or packed and waiting) | gte | orders | 8 | 1 | 50 | yes | 15 |
| `order_waiting_too_long_alert` | Order waiting too long | minutes the oldest packed order has waited | gt | minutes | 8 | 1 | 8 (policy) | yes | 10 |
| `frozen_order_waiting_alert` | Frozen order waiting | minutes the oldest packed frozen order has waited | gt | minutes | 5 | 1 | 8 | no | 10 |
| `sla_dip_alert` | SLA dip | 10-minute SLA % for the last completed hour | lt | percent | 80 | 50 | 100 | no | 60 |

Measure definitions:
- "Packed orders waiting for a rider" are orders with status `packed_waiting_rider`. "Available riders" are riders with status `available`.
- With zero available riders, the rider shortage alert fires whenever any order is waiting.
- The default of 8 for orders piling up fires in the backlog and rain scenarios (10 orders each) and not in normal (4).

**Batching.** Rules can only make batching stricter than the batching policy (DD-BATCH-001).

| Code | Name | Type | Default | Manager can set | Policy basis |
| --- | --- | --- | --- | --- | --- |
| `cold_chain_isolation` | Cold-chain isolation | boolean | on, **locked** | nothing; shown so the rule is visible and cited | frozen and ice-cream orders are single-drop only |
| `surge_only_batching` | Batch only when short of riders | boolean | off | on | batching is a surge tool, not a default. When on, the assistant proposes batches only while the rider shortage alert condition holds or a surge is declared |

**Incentive.**

| Code | Name | Type | Unit | Default | Min | Max |
| --- | --- | --- | --- | --- | --- | --- |
| `incentive_cap` | Surge incentive cap per shift | number | inr | unset: no incentive is proposed until the manager sets one | 0 | open decision |

**Greeting.**

| Code | Name | Type | Default | Allowed values |
| --- | --- | --- | --- | --- |
| `briefing` | What to show when I say hi | view_list | `rider_stats`, `order_queue` | `rider_stats`, `order_queue`, `oldest_order_age`, `last_handover_note` |

On a greeting, the assistant renders each view from live data with its "as of" time. A view whose data is unavailable is reported as unavailable; the assistant never fills in numbers. All views except `last_handover_note` need the live tools.

### Table: `app.store_preferences`

A manager's values for catalogue items.

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | no | `gen_random_uuid()` | primary key |
| `store_id` | varchar(32) | no | | scope |
| `manager_id` | varchar(32) | no | | whose setting it is |
| `code` | varchar(48) | no | | foreign key to `preference_definitions.code` |
| `enabled` | boolean | no | | |
| `value` | jsonb | yes | | a number, boolean, choice or list of views, matching the item's `value_type` |
| `options` | jsonb | yes | | alerts only (below) |
| `status` | varchar(16) | no | `'active'` | a `PreferenceStatus` |
| `created_at` | timestamptz | no | `now()` | |

Indexes and checks:
- A partial unique index on `(store_id, manager_id, code) WHERE status = 'active'`: one active setting per manager and item.
- An index on `(store_id, manager_id, status)` to load a manager's active settings every question.
- `status` takes enum values only.

Alert options, all optional:

```json
{"cooldown_min": 15, "days": ["sat", "sun"], "start": "19:00", "end": null}
```

- With no window, the alert applies at all times.
- `days` are `Weekday` values. Times use Asia/Kolkata.
- A null `end` means end of day, since the store closing time is undefined. A window may cross midnight (for example 19:00 to 02:00).
- `cooldown_min` is between 5 and 240. When absent, the item's default cooldown applies.

Example: "on weekends after 7pm, alert me when pending orders per available rider goes above 1.5" is stored as `code = rider_shortage_alert`, `enabled = true`, `value = 1.5`, `options = {"days": ["sat", "sun"], "start": "19:00"}`.

### Effective settings

For each catalogue item, the effective setting is the manager's active row if one exists, and otherwise the item's defaults. Locked items always use their defaults.

- A change inserts a new active row and marks the previous one `superseded`, in one transaction.
- "Reset to default" marks the active row `removed`.
- Rows are never edited in place, which keeps the history.

### Validation

Code checks every value against its definition before saving:

| Value type | Allowed |
| --- | --- |
| `number` | between `min_value` and `max_value`. For items with a policy limit, the limit is the bound, so a manager can only make them stricter |
| `boolean` | true or false; locked items cannot be changed |
| `choice` | one of `allowed_values` |
| `view_list` | a non-empty list drawn from `allowed_values`, no repeats |

A value outside the limits is rejected with a plain explanation of the limit. A request for something not in the catalogue (for example "alert me when riders speed") is declined, and the assistant lists what can be configured.

### Write path

1. The manager writes something like "Remember: on weekends after 7pm, alert me above 1.5 orders per rider," "only batch when we're short of riders," or "when I say hi, show rider stats and the order queue."
2. An LLM call maps it to a catalogue code and a value using structured output, choosing only from the catalogue.
3. Code validates it against the definition.
4. If it replaces an active setting, ask the manager to confirm.
5. Save it and reply with exactly what was stored. The manager can undo it or reset it to the default.

If the mapping fails or the request is ambiguous, ask a clarifying question and save nothing.

### Conflicts

| Case | Handling |
| --- | --- |
| New setting vs active setting | Ask the manager to replace or keep |
| New setting vs policy limit | Reject, explain the limit |
| Proposed action vs stored setting | Surface the conflict; do not silently override. A code-level check arrives with the Week 3 guardrails |

## 3. Handover notes

Table `app.handover_notes`:

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid | primary key |
| `store_id` | text | scope |
| `manager_id` | text | who wrote the note |
| `shift` | text | shift label, e.g. `2026-10-04 evening` |
| `note` | text | free text |
| `created_at` | timestamptz | |

- Free text, short.
- Only the most recent shift's notes (those sharing the `shift` value of the newest note) are shown to the assistant as reference data. Older notes stay in the table and never expire.
- Notes are not rules and never override policy or preferences.

## 4. Reading preferences and notes

At the start of every question, load the manager's effective settings (active rows merged with catalogue defaults) and the last shift's handover notes for the store. Add them to the prompt as a `<preferences>` block and a `<handover_notes>` block. The system prompt already allows for stored preferences (Sources of truth, item 3).

## 5. Conversation summary

Each conversation has at most one summary, stored on its `conversations` row (`summary` and `summary_covers_to`). There is no separate summaries table.

Purpose: keep long chats within the model's context. Full history is passed on every question today, which gets slow and expensive in a long peak and eventually overflows. The summary replaces older messages.

How it works:
- When the history grows past a limit, the older messages are summarized, together with the previous summary if there is one.
- The result is saved in `summary`, and `summary_covers_to` records the position of the last message it covers. The same row is updated in place; old summaries are not kept.
- The prompt is the summary plus every message after `summary_covers_to`, word for word.

Principles:
- The messages are the source of truth. A summary can always be discarded and regenerated.
- Preferences are loaded separately every question, so summarizing never drops a stored rule.
- The summarizer must not add facts. Figures in a summary are historical and labeled with their time.
- Advisory only. A summary never overrides policy, preferences or tool data.
- Because it lives on the conversation, a restart or refresh keeps it, with no repeated model call.

A handover note drafted from a chat is generated on request and saved by the manager as a `handover_notes` row. It is not a separate stored summary.

Open question: is the limit a token budget or a message count?

## 6. Dreaming (suggestions)

A background review reads past conversations and proposes things the assistant should "know" about its manager. It proposes only. Nothing it finds is applied until the manager accepts it.

What it looks for:
- **Personalisation:** repeated requests that suggest a briefing ("you asked for rider stats at the start of most shifts").
- **Alerts:** repeated concerns that suggest turning on or tuning an alert ("you asked about the frozen queue three times").
- **Insights:** observed patterns, as plain text ("backlogs repeat on Friday evenings"). These have nothing to apply and can only be read or dismissed.

How it works:
1. A run reads conversations updated since the previous run, so each run only reads new messages. Where the previous run time is kept is decided when dreaming is built.
2. An LLM proposes candidates as catalogue settings (a code, a value and options), the same shape as section 2.
3. Code validates them exactly like manager-stated settings, including the catalogue limits and the minimum-occurrence rule below.
4. Valid candidates are saved as `pending` rows in `suggestions`, each with the messages that support it.
5. The manager accepts or dismisses each one. Accepting writes a normal `store_preferences` row through the standard validated write path, and marks the suggestion `accepted`.

Rules:
- A candidate needs a minimum number of supporting occurrences before it is saved (value decided at implementation).
- Stored text is user-written data. The review must not follow instructions found in it.
- Suggestions go through the same safety checks as preferences, so it cannot propose anything unsafe.
- Dismissed suggestions are not proposed again for the same evidence.
- The MVP run is started manually (a CLI command or a UI button). Scheduling comes later.

Table `app.suggestions`:

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid | primary key |
| `store_id` | text | scope |
| `manager_id` | text | who it is for |
| `kind` | text | `setting` or `insight` |
| `payload` | jsonb | for a setting, `{"code", "enabled", "value", "options"}`; for an insight, `{"text": ...}` |
| `reason` | text | plain-language explanation shown to the manager |
| `evidence` | jsonb | supporting messages as conversation id and message position pairs |
| `status` | text | `pending`, `accepted` or `dismissed` |
| `created_at` | timestamptz | |

## Parked: Resolution notes

Not part of the current build. Kept here so the design is not lost.

Table `app.resolution_notes`, scoped per store:

| Column | Notes |
| --- | --- |
| `id`, `store_id` | |
| `title` | short label |
| `situation` | symptoms and conditions |
| `root_cause` | the RCA |
| `actions_taken` | ordered steps |
| `outcome` | `worked`, `partly_worked` or `did_not_work` |
| `embedding` | vector of situation, root cause and actions |
| `created_at` | figures in the note are historical as of this date |

Write: the manager asks to save a diagnosis, the LLM drafts the note from the conversation, the manager edits or approves, code runs the safety check, then it is saved and embedded.

Read: embed the question, search active notes for the store, keep matches above a similarity threshold, and add them as a separate `<past_cases>` block. These are kept out of `document_chunks` because they differ in scope, mutability and trust.

Guardrails:
- Reject notes that describe unsafe tactics (speeding, skipping breaks, over-hours work, penalties). Run the same check when notes are retrieved.
- The assistant presents notes as "your note from <date>", advisory only. Policy and guardrails always win.
- Figures in a note are history, labeled with `created_at`, never live data.
- Managers can edit and delete notes.

## Code layout

| Path | Contents |
| --- | --- |
| `backend/database/models.py` | `Conversation`, `PreferenceDefinition`, `StorePreference`, `HandoverNote`, `Suggestion` |
| `backend/alembic/versions/` | One migration per phase |
| `backend/domain/memory.py` | Enums, value and alert-option models |
| `backend/queries/conversations.py` | Start conversation, append message, load latest, list recent, resume |
| `backend/queries/preferences.py` | Database access for preferences and handover notes |
| `backend/service/conversations.py` | Orchestration around the answer call |
| `backend/service/memory.py` | Mapping requests to catalogue items, validation, effective settings, conflict logic |
| `backend/service/dreaming.py` | Background review: read new messages, propose, save suggestions |
| `backend/ui/gradio_app.py` | Restore on load, save on each question, chat sidebar |
| `backend/tests/` | Tests for each of the above |

## Testing

Conversations:
- A conversation survives a page refresh and an app restart.
- A failed model call keeps the manager message and appends no reply.
- Loading a scenario or New chat starts a new conversation and keeps the old one stored.
- The sidebar lists non-empty chats newest first and highlights the open one.
- Opening a past chat makes it current, and the next question continues it.
- Message order is stable and matches the order of the appends.
- With a summary present, the prompt is the summary plus only the messages after `summary_covers_to`.

Preferences:
- A preference stored in session 1 is applied in a new session without being repeated.
- A conflicting request is flagged instead of overridden.
- Record both transcripts as task 17 evidence.
- With no stored row, the effective setting equals the catalogue default.
- A value outside `min_value` and `max_value` is rejected with an explanation.
- A locked item cannot be changed.
- A request for something not in the catalogue is declined.
- Changing a setting supersedes the old row; reset marks it `removed`.
- A briefing with an unknown view is rejected.

Dreaming:
- A run reads only conversations updated since the previous run.
- A candidate with too few occurrences is not saved.
- Suggestions are saved as `pending` and change no preference until accepted.
- Accepting a suggestion creates a valid `store_preferences` row.
- A suggestion that violates a policy floor or safety rule is not saved.

## Decisions

1. Manager identity: a single demo manager (Karthik) per store, recorded in `manager_id` on conversations, preferences and handover notes. Everything is scoped to the scenario's `store_id` and the manager, so a new browser session or restart still recalls it.
2. Policy floors: code constants holding default values. The values are decided during implementation.
3. Handover notes: only the last shift's notes are given to the assistant. Old notes are kept and never expire.
4. Resolution notes: parked for later. Similarity threshold and top-k are decided when that phase starts.
5. A conversation holds its messages as a JSON list of `{who, what, when}` and at most one summary, all on the `conversations` row. No messages or summaries table.
6. A new conversation starts on first use, New chat, or a scenario load. No idle timeout and no status column; the latest by `updated_at` is the current one. No `ended_at`.
7. MVP columns only. Deferred columns are listed below.
8. All configurable items (5 alerts, 2 batching rules, incentive cap, greeting) are predefined in `preference_definitions`, with defaults and min and max values. Managers' values live in `store_preferences`. Alert evaluation and any fired-alert table are pending a team discussion.
9. All preferences are per manager.
10. Dreaming is a suggest-only background review, backed by a `suggestions` table.
11. Past conversations are browsed and continued from a Claude-style sidebar. Continuing a chat makes it the most recently updated one.

## Deferred

Left out of the MVP on purpose, to add when needed:

| Table | Deferred columns | Needed for |
| --- | --- | --- |
| `conversations` | `scenario_key` | Evals and reporting by scenario |
| `store_preferences` | `superseded_by`, `source_text`, source message | Audit trail |
| `preference_definitions` | more items, such as a Rain started alert | Needs `is_raining` stored in the database |
| `suggestions` | `accepted_at`, `preference_id` | Linking an accepted suggestion to the preference it created |

## Open decisions

1. Conversation retention: keep forever for now, or set a limit?
2. Summary limit: token budget or message count?
3. Briefing trigger: greeting only (assumed), or also automatically on the first message of a new conversation?
4. Dreaming: suggest-only (assumed, never auto-apply) and in this document (assumed), or a separate one?
5. Where suggestions appear: a panel in the UI, or raised by the assistant at the start of a session?
6. Alert evaluation and any fired-alert table: pending the team discussion.
7. Incentive cap upper bound (for example ₹500 per shift).
