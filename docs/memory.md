# DispatchDesk memory design

Status: section 1 (conversation history, with the chat sidebar) and section 2 (preferences, except evaluating alerts and rendering the briefing) are implemented. The other sections are a draft for review and are not implemented yet.

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
| 1 | `conversations` | One row per chat. Holds its messages as a JSON list and its summary. | `id`, `store_id`, `manager_id`, `messages`, `summary`, `summary_covers_to`, `summarized_at`, `title`, `created_at`, `updated_at` |
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
| `title` | varchar(120) | yes | | short title, set once after the first answer (below) |
| `summarized_at` | timestamptz | yes | | when the summary was last saved |
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

### Titles and timestamps

- **Title:** after a new chat's first answer is shown, a separate model call writes a 2 to 6 word title from the first question and answer (prompt in `rag_data/prompts/conversation_title.md`). It runs after the reply, on its own queue, so it never delays the answer or the next question. The title is set only once. If the call fails, the title stays empty and the sidebar shows the first question instead.
- **Timestamps:** every message shows its time under the bubble, from its stored `when`: the time for today, and the day and time for older messages, in IST. The time is display only; the model receives the plain message text.

### Chat sidebar

A sidebar, like Claude's, lets the manager browse and continue past chats:

- **New chat** at the top starts a new conversation.
- **Recent** lists past conversations, newest first, by title (or first question until titled). Empty conversations are not listed. The open conversation is highlighted.
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

A value outside the limits is rejected with a plain explanation of the limit. Only catalogue items appear in the Settings tab, so nothing outside the catalogue can be configured.

### Write path: the Settings tab

Settings are changed only in the **Settings** tab, never through chat, so every change is deterministic.

- The tab shows every catalogue item as a form control: an on/off switch, a threshold with its allowed range, days, start and end time, and a cooldown for each alert; switches for the batching rules (cold-chain shown as locked); an amount for the incentive cap; and view checkboxes for the greeting.
- **Save settings** checks each item against its definition and saves only items whose value changed. Each saved or rejected item is reported next to the button; a rejected item does not block the others.
- **Reset to default** on an item marks its active row `removed`.
- An **Active settings** block, pinned at the bottom of the chat sidebar, lists what is on with its value, tags values the manager changed as "yours", and collapses everything off into one line. **Edit** opens the Settings tab. It refreshes on page load and after every save or reset.
- The assistant reads the settings (the `<preferences>` block) and applies them, but cannot change them. Its prompt tells it to point the manager to the Settings tab and never to claim a setting was changed.

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

Status: implemented.

Each conversation has at most one summary, on its own row: `summary`, `summary_covers_to` (the index of the last message folded in), and `summarized_at`. The `messages` list is never edited; only the summary rolls forward. There is no separate summaries table.

Purpose: keep prompts small in long chats, and have a complete summary of every chat once it goes quiet (for reopening, handover and dreaming).

Terms, as constants in `backend/constants.py`:

| Term | Value | Meaning |
| --- | --- | --- |
| Raw messages | | messages after `summary_covers_to` (all, if there is no summary) |
| Recent window | 6 messages | always sent to the model word for word |
| Count limit | 16 messages | raw messages above this trigger folding |
| Size limit | 3,000 tokens | raw text above this (characters / 4) triggers folding |
| Idle time | 30 minutes | since the last message's own `when` |
| Job interval | 5 minutes | how often the scheduler looks for idle chats, at most 10 per run |

Triggers:
- **After an answer:** if the raw messages exceed the count or size limit, fold every raw message except the recent window. Runs on its own queue after the reply, so it never delays an answer.
- **Idle (scheduler):** a background job started with the app finds chats whose last message is older than the idle time and whose summary does not reach the last index, and folds every message, including the recent window. Selection uses message positions and the last message's time, not `updated_at`.

Folding: the previous summary plus the messages to add go to the model with `rag_data/prompts/conversation_summary.md`, which keeps questions, diagnoses, proposals with their approval state, quoted figures as earlier figures, and open follow-ups, and forbids new facts. The save is conditional on `summary_covers_to` being unchanged since the run started, so overlapping runs (for example two app instances) never overwrite each other. It does not change `updated_at`.

Prompt: the summary goes in a `<conversation_summary>` block, followed by the raw messages after `summary_covers_to`, but always at least the recent window, so a chat resumed after an idle summary keeps its last exchanges in full. If summarizing fails, nothing changes and the full raw history is sent.

The summary is used by the model only; the chat shows the stored messages unchanged.

Worked example (count limit, short messages): at 18 messages, 0 to 11 are folded and `summary_covers_to` becomes 11; at 30, 12 to 23 are folded and it becomes 23. The model never receives more than 16 raw messages plus the summary.

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
| `backend/domain/memory.py` | Enums and the alert-option model |
| `backend/queries/conversations.py` | Start conversation, append message, load latest, list recent, resume |
| `backend/queries/handover_notes.py` | Database access for handover notes |
| `backend/service/conversations.py` | Orchestration around the answer call |
| `backend/service/preferences.py` | Validation, effective settings, save and reset, the `<preferences>` prompt block |
| `backend/ui/settings.py` | The Settings tab |
| `backend/queries/preferences.py` | Catalogue, active values, save with supersede, remove |
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
- The Settings form saves only changed items and reports rejected ones.
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
2. Briefing trigger: greeting only (assumed), or also automatically on the first message of a new conversation?
3. Dreaming: suggest-only (assumed, never auto-apply) and in this document (assumed), or a separate one?
4. Where suggestions appear: a panel in the UI, or raised by the assistant at the start of a session?
5. Alert evaluation and any fired-alert table: pending the team discussion.
6. Incentive cap upper bound (for example ₹500 per shift).
