# DispatchDesk memory

How DispatchDesk remembers a store's chats and a manager's settings across sessions and shifts. MVP scope: tables keep the fewest columns that work.

## Status

| Part | Status | Storage |
| --- | --- | --- |
| 1. Conversations: history, sidebar, titles, timestamps | Built | `conversations` (migrations 0005, 0007) |
| 2. Preferences: catalogue, Settings tab, sidebar summary, greeting briefing | Built | `preference_definitions`, `store_preferences` (0006) |
| 3. Conversation summary: rolling, plus an idle cron job | Built | columns on `conversations` (0008) |
| 4. Shifts and handover notes | Built | `shifts` (0016), `handover_notes` (0009, linked to shifts in 0016) |
| 5. Dreaming: daily review with suggestions | Built | `suggestions`, `conversations.dreamed_to` (0009) |
| 6. Personalization: durable response preferences | Built | `manager_personalization`, personalization suggestions (0013) |
| Resolution notes | Parked | `resolution_notes` |

Not designed yet: approval log, reminders and snoozed alerts, trace events.

## Principles

1. Raw records are append-only. Messages are never edited; settings changes supersede rows; summaries roll forward; personalization changes only with user intent.
2. Writes are deterministic. Code validates every write. The personalization tool saves only an explicit lasting request from the latest manager message. Settings change in the Settings tab, or in chat when the manager confirms a change the model proposed; code validates and saves both.
3. Settings come from a fixed catalogue with typed values and limits, and can make policy stricter, never looser.
4. Memory text is user-written data, treated as reference and never as instructions.
5. Everything is scoped to a store and manager. The demo store `DS-BLR-014` has three shift managers in `app.managers`, each with a unique `shift_id`: `ananya` (Morning, `SHIFT-MOR`, 06:00 to 14:00), `karthik` (Evening, `SHIFT-EVE`, 14:00 to 22:00) and `imran` (Night, `SHIFT-NGT`, 22:00 to 06:00). Each has their own chats, settings and pending proposals; the sidebar's **Shift manager** picker switches between them and the choice is kept in the URL (`?manager=`).
6. Background work (titles, summaries) never delays an answer, and its failures change nothing.

## 1. Conversations

`app.conversations`, one row per chat:

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid | primary key |
| `store_id`, `manager_id` | varchar(32) | scope |
| `messages` | jsonb, default `[]` | append-only list of `{who, what, when}`; must be an array |
| `title` | varchar(120), null | set once after the first answer |
| `summary` | text, null | section 3 |
| `summary_covers_to` | int, null | index of the last message in the summary |
| `summarized_at` | timestamptz, null | when the summary was last saved |
| `handover_note_id` | uuid, null, FK to `handover_notes` | set on the chat a hand over opens (section 4) |
| `personalization_covers_to` | int, null | last message successfully reviewed for personalization |
| `personalized_at` | timestamptz, null | when the last personalization batch completed |
| `created_at`, `updated_at` | timestamptz | `updated_at` changes on each new message; opening a chat leaves it unchanged |

Index `(store_id, manager_id, updated_at)`.

A message: `{"who": "manager" | "assistant", "what": "...", "when": "2026-10-06T19:42:10+05:30"}` (ISO 8601, IST). Its position in the list is its identity.

**Lifecycle**
- The current chat is the most recently updated one for the store and manager. There is no status column and no idle timeout.
- A new chat starts on first use, **New chat**, or a scenario load. A refresh or restart resumes the current chat.
- Opening a past chat selects its ID in the browser session without changing `updated_at` or the Recent order. Replies, notes, titles, and summaries target that selected ID. The URL carries the chat ID so refreshes reopen it, and the page title follows the chat title.
- Two browser tabs write to the same current chat; accepted for the demo.

**Write path:** append the manager's message first (one atomic `UPDATE`), call the model, then append the reply. On failure nothing is appended and the error is logged; the question is kept.

**UI**
- Sidebar: New chat, then Recent chats by title (or first question until titled), newest first, open one highlighted; empty chats hidden. On phones the sidebar starts closed and closes after a choice.
- Title: after the first answer, a separate model call writes 2 to 6 words (`prompts/conversation_title.md`), on its own queue. Set once; on failure the first question is shown.
- Timestamps: each bubble shows its stored time in IST, as the time for today or the day and time for older messages. Display only; the model gets plain text.
- The header and workspace switcher stay pinned while scrolling.

**Retention:** kept in the project's PostgreSQL only; deleting removes the whole chat; no expiry yet.

## 2. Preferences

A catalogue of configurable items, `preference_definitions`, seeded by migration; each manager's values in `store_preferences`. No row means the default applies.

**Enums** are stored as plain strings and mirrored by Python `StrEnum`s in `domain/memory.py`. Postgres `ENUM`s are avoided because they are awkward to change.

| Enum | Values |
| --- | --- |
| `PreferenceCategory` | `alert`, `batching`, `incentive`, `briefing` |
| `ValueType` | `number`, `boolean`, `choice`, `view_list` |
| `Unit` | `orders_per_rider`, `orders`, `minutes`, `percent`, `inr` |
| `AlertOperator` | `gt`, `gte`, `lt` |
| `BriefingView` | `rider_stats`, `order_queue`, `oldest_order_age`, `last_handover_note` |
| `Weekday` | `mon` to `sun` |
| `PreferenceStatus` | `active`, `superseded`, `removed` |

**`app.preference_definitions`:** `code` (PK), `category`, `name`, `description`, `value_type`, `unit`, `operator`, `default_value` (jsonb), `min_value`, `max_value`, `allowed_values` (text[]), `default_enabled`, `default_cooldown_min`, `locked`. Checks keep enum columns valid and `min_value <= max_value`.

**The catalogue (9 items)**

| Code | Rule | Default | Limits |
| --- | --- | --- | --- |
| `rider_shortage_alert` | packed orders waiting ÷ available riders `>` value; fires on any waiting order with no rider available | on, 2, 15 min | 0.5 to 2 (policy) |
| `orders_piling_up_alert` | orders picking or packed and waiting `≥` value | on, 8, 15 min | 1 to 50 |
| `order_waiting_too_long_alert` | oldest packed order's wait `>` value minutes | on, 8, 10 min | 1 to 8 (policy) |
| `frozen_order_waiting_alert` | oldest packed frozen order's wait `>` value minutes | off, 5, 10 min | 1 to 8 |
| `sla_dip_alert` | last completed hour's 10-minute SLA `<` value % | off, 80, 60 min | 50 to 100 |
| `cold_chain_isolation` | frozen orders are never batched | on, **locked** | policy |
| `surge_only_batching` | batch only while riders are short or a surge is declared | off | on or off |
| `incentive_cap` | most spent on surge incentives per shift; none proposed while unset | unset | ₹0 to ₹500 (placeholder) |
| `briefing` | views to show when the manager says hi | rider stats, order queue | any of the 4 views |

**`app.store_preferences`:** `id`, `store_id`, `manager_id`, `code` (FK to the catalogue), `enabled`, `value` (jsonb), `options` (jsonb, alerts only), `status`, `created_at`. A partial unique index allows one `active` row per store, manager and code.

Alert `options`, all optional: `{"days": ["sat", "sun"], "start": "19:00", "end": null, "cooldown_min": 20}`. No window means always; a null `end` means end of day; cooldown is 5 to 240 minutes.

**Rules**
- A change inserts a new `active` row and marks the old one `superseded`, in one transaction. Reset marks it `removed`. Saving an unchanged value stores nothing.
- Validation against the definition: numbers within min and max, booleans only for unlocked items, choices from `allowed_values`, view lists non-empty and without repeats, alert options well formed. A rejected item is explained and doesn't block the others.

**Settings tab**
- Categories in a left list (Alerts, Batching, Incentive, Greeting), one panel at a time.
- One Save for all items, and a Reset to default per item.
- The incentive cap is a single amount field: entering an amount turns it on, clearing it turns it off.

**Active settings:** a block pinned at the bottom of the chat sidebar lists what is on, tags the manager's own values "yours", and puts everything off on one line. Edit opens Settings; it refreshes on load and after each save or reset.

**Chat:** the model can propose a change with the `propose_setting_change` tool (`service/setting_changes.py`). Code merges the request into the current setting (unmentioned fields keep their values), validates it like the Settings tab, and shows a card with the current and new value. Only **Confirm** saves it, through the same `PreferenceService` path; **Cancel** discards it. Both add a note to the chat. Details: [alerts.md](alerts.md), section 1.

**Read path:** every question includes a `<preferences>` block with each item's effective value, whether it is customized, its limits and its description. The model applies operational settings and can only propose changes to them, never save them.

**Greeting briefing:** a bare greeting ("hi", "good evening", "hey team") is answered by code, not the model, with the manager's chosen views in their order. Live views (rider stats, order queue, oldest order age) come from one call to the assistant's own `get_live_dispatch_status` tool, recorded in the answer's trace. The handover view is the chat's own note in a handover chat, else the store's latest. Missing or stale data is said plainly; a greeting with more in it ("hi, orders are piling up") goes to the assistant. Code: `service/briefing.py`.

## 3. Conversation summary

`summary`, `summary_covers_to` and `summarized_at` on `conversations`. Only these change; `messages` stays raw. Raw messages are those after `summary_covers_to`.

| Constant (`constants.py`) | Value |
| --- | --- |
| Recent window, always sent raw | 6 messages |
| Count limit on raw messages | 16 |
| Size limit on raw text (characters ÷ 4) | 3,000 tokens |
| Idle time, from the last message's `when` | 10 minutes |
| Chats per CLI job run | 10 |

**Triggers**
- After an answer, on its own queue: if raw messages exceed either limit, fold all but the recent window.
- On demand: **Summarize now** folds every message, the recent window included.
- The `uv run python cli.py summaries` command folds every message of chats idle for 10 minutes that the summary doesn't fully cover, then retries pending personalization batches. Railway cron runs it every five minutes. Gradio starts no scheduler. Selection uses positions and the last message's time, not `updated_at`.

**Folding:** the previous summary plus the new slice go to `prompts/conversation_summary.md`, which keeps questions, diagnoses, proposals with their approval state, earlier figures marked as earlier, and open follow-ups, and forbids new facts. The save applies only if `summary_covers_to` is unchanged since the run started, sets `summarized_at`, and leaves `updated_at` alone.

**Read path:** a `<conversation_summary>` block, then the raw messages, but always at least the recent window. On failure the full raw history is sent.

**UI:** a row pinned under the header while the chat scrolls, shown for any chat with messages:
- A collapsed card, "Summary of earlier messages · covers 12 of 30 · updated 19:42", or "Not summarized yet · 4 messages". Expanded, it shows the summary in a capped, scrollable area, with a note that the assistant wrote it for its own context. All messages stay visible below.
- **Summarize now** (or **Update summary** once one exists) folds every message so far, the latest included, into the summary and opens the card. If nothing is new, a notice says so.
- The row refreshes on page load, opening a chat, New chat, a scenario load, and after each answer's summarization step. Idle-job summaries appear the next time the chat is loaded or opened. The sidebar has no summary marker.

Example: at 18 messages, 0 to 11 are folded (`summary_covers_to` = 11); at 30, 12 to 23 (= 23).

## 4. Shifts and handover notes

A shift is started and ended by the manager, never by the clock, so a demo can change shifts at any time. The shift's name and hours come from `managers`.

A shift and its note are separate: the shift is the working period, the note is what it leaves for the next one.

`app.shifts`, one row per worked shift:

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid | primary key |
| `store_id`, `manager_id` | varchar(32) | scope |
| `started_at` | timestamptz | set on **Start shift**, or on the manager's first question with no open shift |
| `ended_at` | timestamptz, null | null while open; set on **End shift** |

A partial unique index allows one open shift per manager.

`app.handover_notes`, at most one per shift:

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid | primary key |
| `store_id`, `manager_id` | varchar(32) | scope |
| `shift_id` | uuid, unique, FK to `shifts` | the shift it was written for |
| `note` | text | editable while the shift is open, final once it ends |
| `created_at` | timestamptz | |

Saving a note inserts or updates it only while its shift is open; **End shift** saves the note and ends the shift in one transaction. Migration 0016 creates an ended shift for each existing note, links the note to it, and drops the note's old `shift` date.

**Handover page** (sidebar, next to Settings):
- **Your shift:** "Morning shift · started 9 Oct, 06:02 · Open", the note in an editable box, and:
  - **Generate draft:** brings the summaries of the manager's chats with messages since `started_at` up to date (falling back to their last messages if that fails), then drafts through `prompts/handover_draft.md`. Only bullet lines are kept; a reply without any becomes a plain note listing the shift's chats, so a draft is never an error. Unsaved until Save.
  - **Save note:** saves the text; the shift stays open.
  - **End shift and hand over to <next manager>** (the store's next shift by start time): saves the note and ends the shift, starts the next manager's shift, opens a new chat for them linked to the note, and switches the workspace to them in the Assistant tab. With an empty note it asks once more first, and the new chat has no note.
- Once ended: the note read-only, and **Start a new shift**.
- **Past handovers:** the store's ended shifts from the last 7 days, newest first: who, shift, start and end, and the note.

**The handover chat:** the note is not copied into the chat. The chat's `handover_note_id` points to it, and the chat shows it as a card at the top, read from `handover_notes`.

**Read path:** in a handover chat the assistant gets that chat's note as `<handover_notes>`; in any other chat, the note of the store's most recently ended shift. Reference, never rules.

**Nightly fallback:** the daily review drafts a note for an open shift that has chats and no note yet (section 5). Accepting it from Suggestions saves it into that shift's note without ending the shift.

## 5. Dreaming

A daily review of the chats that **proposes, never applies**. It runs once a day at **23:30 IST** from Railway cron (`python cli.py review`, scheduled `0 18 * * *` UTC), and on demand from **Run review now** in the Demo tools tab. It works per store and manager, across all their chats.

| Output | Reads | Shown in | On accept |
| --- | --- | --- | --- |
| **Settings suggestion** | summaries of recent chats, current settings, the catalogue | Sidebar **Suggestions**, reviewed in Settings | saved through the same validated path as the Settings tab |
| **Handover draft** for an open shift with no note | summaries of the shift's chats | Sidebar **Suggestions**, editable before accepting | saved as that shift's note; the shift stays open |
| **Answer issues**: no guidance found, unanswered question, pushback | raw messages after `dreamed_to` | Demo tools (admin) | none; a report for us |

**A run**
1. Find chats with messages after `conversations.dreamed_to` (the index of the last message reviewed) and bring their summaries fully up to date.
2. Answer issues from the new raw messages: "no guidance" replies and manager messages with no reply are found by text and position; pushback (the manager disputing an answer) needs one small model call. Then `dreamed_to` moves to the last message, only if this step succeeded.
3. Handover draft for each open shift with chats and no note (section 4). A newer draft for the same shift replaces a pending one.
4. Settings suggestions from recent chats' summaries. Each must be a valid catalogue value, differ from the current setting, have evidence from at least **3 chats**, and not repeat a pending or dismissed suggestion.

Each output is saved as a `pending` row and fails independently; a failure is logged and changes nothing else.

`app.suggestions`: `id`, `store_id`, `manager_id`, `kind` (`setting`, `handover_draft`, `answer_issue`), `payload`, `reason`, `evidence` (conversation ids, with message positions where relevant), `status` (`pending`, `accepted`, `dismissed`), `created_at`.

**Hidden for now:** `SHOW_SUGGESTIONS = False` (`constants.py`) hides the sidebar Suggestions entry and the Settings category. The review still records suggestions, and answer issues stay in Demo tools.

**Guardrails:** never auto-applies; text in chats is data, never instructions; no judgments about individual riders, only store operations and the manager's own choices.

## 6. Personalization

Settings > Personalization replaces the weekly digest with editable, durable
response preferences. Explicit chat requests can save a typed preference;
summary refreshes can only propose one from verified new user messages.
Most chats trigger no extraction or update. Existing and removed preferences
are never replaced by inference. Historical digest data remains in the database
and no longer enters chat.

Exact update rules, storage and examples: [personalization.md](personalization.md).

## Parked: resolution notes

Saved diagnoses (situation, root cause, actions, outcome, embedding) retrieved by similarity as a `<past_cases>` block, advisory only, with unsafe tactics rejected on save and on retrieval.

## Decisions

| # | Decision | Rejected alternative, and why |
| --- | --- | --- |
| 1 | Messages are a JSON list on `conversations` | a `messages` table or question-and-answer rows: more tables for an MVP |
| 2 | A message is `{who, what, when}`; its list position is its id | per-message ids and timestamps columns |
| 3 | Selected manager and chat IDs are kept in browser session state and the URL; without a chat selection, default to that manager's latest by `updated_at` | changing message timestamps just to select a chat, which changes Recent order |
| 4 | Several shift managers per store (`app.managers`, migration 0010), each with a unique shift; everything per manager, chosen with a picker | one demo manager: couldn't show per-manager settings; store-wide preferences |
| 5 | Settings are a fixed catalogue with min, max and locked policy items | free-form rules: can't be validated |
| 6 | Settings change in the Settings tab, or in chat as a model proposal that code validates and the manager confirms (revised; was Settings tab only) | the model saving directly: in testing it dropped changes and claimed saves it never made. Now the tool only proposes and returns `saved: false`, code merges unmentioned fields, the card shows exactly what will be saved, and code rewrites a reply that only echoes the tool |
| 7 | Settings changes supersede rows, never edit them | in-place updates lose history |
| 8 | Incentive cap is on when an amount is set | a separate on/off switch, which saved amounts that were off |
| 9 | Titles are model-written after the first answer, set once | first-question titles: less readable |
| 10 | Summaries roll forward on the conversation row, with `summary_covers_to` as an index | a summaries table, or editing messages |
| 11 | Summaries trigger on count and size limits, plus an idle cron job | lazy checks on page load: summaries not ready until a chat is reopened |
| 12 | Summary saves are conditional on the previous position | last write wins: overlapping runs would overwrite each other |
| 13 | Railway cron runs the one-shot CLI summary job; no scheduler runs inside Gradio | an in-process scheduler thread |
| 14 | The summary is shown as a collapsed card above the chat, with all messages kept visible | hiding folded messages behind the card, or a separate panel: confusing or easy to miss |
| 15 | The manager can summarize on demand, folding everything including recent messages; the card is pinned under the header | waiting for the limits or the idle job only |
| 16 | Dreaming produces settings suggestions, a daily handover draft and an answer-issue report; recurring patterns are left to metrics data | patterns from chats: weak evidence |
| 17 | Dreaming runs daily at 23:30 IST from Railway cron (`cli.py review`), plus an admin button; a shift is a calendar day | per-shift runs: shifts are not defined yet |
| 18 | Handover drafts and settings read summaries; answer issues read raw messages after `dreamed_to` | raw messages everywhere: costlier; summaries everywhere: hide pushback and missing answers |
| 19 | Suggestions appear in the sidebar and are reviewed in Settings; answer issues stay in admin | showing the issue report to the manager |
| 20 | Durable personalization is a small typed profile per manager, updated by explicit intent or accepted proposals | A rolling digest mixes temporary facts with preferences and loses them as chats age out |
| 21 | Removed preferences retain a null entry, blocking inference from older chats | Deleting the entry lets old messages recreate it |
| 22 | Shifts are rows the manager starts and ends; the handover note is its own table, one per shift, editable until the shift ends | shifts derived from the clock and manager hours: a demo would have to wait for real time to pass; the note as a column on the shift: mixes the working period with what it leaves behind |
| 23 | Ending is explicit; the draft comes from chats since the shift started; the nightly draft stays as a fallback | ending on the clock; drafting from today's chats only, which misses a night shift's early hours |
| 24 | Handing over opens a new chat for the next manager that links to the note and shows it as a card | copying the note into the chat as its first message: a second copy, and a non-model message among the raw messages |
| 25 | The greeting briefing is built in code from one traced tool call, for a bare greeting only | asking the model to follow the Greeting setting: it ignored it and treated "Hi" as off topic |

## Deferred

| Table | Columns | Needed for |
| --- | --- | --- |
| `conversations` | `scenario_key` | evals and reporting by scenario |
| `store_preferences` | `superseded_by`, `source_text` | audit trail |
| `preference_definitions` | a Rain started alert | `is_raining` stored in the database |
| `suggestions` | `accepted_at`, `preference_id` | linking a suggestion to its setting |

## Open decisions

1. Conversation retention: keep forever, or set a limit?
2. Incentive cap maximum (₹500 placeholder).
3. Alert evaluation and any fired-alert table, pending the team discussion.

## Code

| Path | Contents |
| --- | --- |
| `domain/memory.py` | enums, alert options |
| `database/models.py` | `Conversation`, `PreferenceDefinition`, `StorePreference` |
| `queries/conversations.py`, `queries/preferences.py` | database access |
| `service/conversations.py` | ask, history, titles, sidebar entry points |
| `service/preferences.py` | effective settings, validation, save and reset, `<preferences>` block |
| `service/managers.py`, `queries/managers.py` | the store's shift managers, choosing one |
| `service/setting_changes.py` | chat setting changes: tool, merge, validate, confirm |
| `service/summaries.py`, `cli.py` | summary folding, one-shot idle job |
| `service/dreaming.py` | daily review orchestration and failure report |
| `service/suggestions.py`, `service/handover.py`, `service/personalization.py` | scoped suggestion actions, shifts and handover notes, durable personalization |
| `ui/handover.py` | Handover page and the handover card in chat |
| `queries/dreaming.py` | review persistence and atomic suggestion/draft actions |
| `ui/gradio_app.py` | application composition and event wiring |
| `ui/chat.py`, `ui/sidebar.py`, `ui/scenarios.py`, `ui/summary.py` | chat, history, manager selection, scenario inspection and summary presentation |
| `ui/settings.py`, `ui/suggestions.py`, `ui/personalization.py` | settings, suggestions and editable personalization |
| `resources/prompts/` | system, title, summary and dreaming prompts, including strict `personalization.md` extraction rules |
