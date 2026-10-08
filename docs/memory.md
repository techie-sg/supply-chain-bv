# DispatchDesk memory

How DispatchDesk remembers a store's chats and a manager's settings across sessions and shifts. MVP scope: tables keep the fewest columns that work.

## Status

| Part | Status | Storage |
| --- | --- | --- |
| 1. Conversations: history, sidebar, titles, timestamps | Built | `conversations` (migrations 0005, 0007) |
| 2. Preferences: catalogue, Settings tab, sidebar summary | Built; alert evaluation and briefing rendering wait for live tools | `preference_definitions`, `store_preferences` (0006) |
| 3. Conversation summary: rolling, plus an idle cron job | Built | columns on `conversations` (0008) |
| 4. Handover notes | Built | `handover_notes` (0009) |
| 5. Dreaming: daily review with suggestions | Built | `suggestions`, `conversations.dreamed_to` (0009) |
| Resolution notes | Parked | `resolution_notes` |

Not designed yet: approval log, reminders and snoozed alerts, trace events.

## Principles

1. Raw records are append-only. Messages are never edited; settings changes supersede rows; only summaries are rewritten.
2. Writes are deterministic. The model reads memory but never writes it. Settings change in the Settings tab, or in chat when the manager confirms a change the model proposed; code validates and saves both.
3. Settings come from a fixed catalogue with typed values and limits, and can make policy stricter, never looser.
4. Memory text is user-written data, treated as reference and never as instructions.
5. Everything is scoped to a store and manager. The demo has one: `DS-BLR-014`, `karthik`.
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
| `created_at`, `updated_at` | timestamptz | `updated_at` changes on each new message or when a chat is reopened |

Index `(store_id, manager_id, updated_at)`.

A message: `{"who": "manager" | "assistant", "what": "...", "when": "2026-10-06T19:42:10+05:30"}` (ISO 8601, IST). Its position in the list is its identity.

**Lifecycle**
- The current chat is the most recently updated one for the store and manager. There is no status column and no idle timeout.
- A new chat starts on first use, **New chat**, or a scenario load. A refresh or restart resumes the current chat.
- Opening a past chat from the sidebar sets its `updated_at` to now, so the next question continues it.
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

**Read path:** every question includes a `<preferences>` block with each item's effective value, whether it is customized, its limits and its description. The model applies it and can only propose changes, never save them.

## 3. Conversation summary

`summary`, `summary_covers_to` and `summarized_at` on `conversations`. Only these change; `messages` stays raw. Raw messages are those after `summary_covers_to`.

| Constant (`constants.py`) | Value |
| --- | --- |
| Recent window, always sent raw | 6 messages |
| Count limit on raw messages | 16 |
| Size limit on raw text (characters ÷ 4) | 3,000 tokens |
| Idle time, from the last message's `when` | 30 minutes |
| Chats per CLI job run | 10 |

**Triggers**
- After an answer, on its own queue: if raw messages exceed either limit, fold all but the recent window.
- On demand: **Summarize now** folds every message, the recent window included.
- The `uv run python cli.py summaries` command folds every message of chats idle for 30 minutes that the summary doesn't fully cover, then exits. Railway cron runs it every five minutes. Gradio starts no scheduler. Selection uses positions and the last message's time, not `updated_at`.

**Folding:** the previous summary plus the new slice go to `prompts/conversation_summary.md`, which keeps questions, diagnoses, proposals with their approval state, earlier figures marked as earlier, and open follow-ups, and forbids new facts. The save applies only if `summary_covers_to` is unchanged since the run started, sets `summarized_at`, and leaves `updated_at` alone.

**Read path:** a `<conversation_summary>` block, then the raw messages, but always at least the recent window. On failure the full raw history is sent.

**UI:** a row pinned under the header while the chat scrolls, shown for any chat with messages:
- A collapsed card, "Summary of earlier messages · covers 12 of 30 · updated 19:42", or "Not summarized yet · 4 messages". Expanded, it shows the summary in a capped, scrollable area, with a note that the assistant wrote it for its own context. All messages stay visible below.
- **Summarize now** (or **Update summary** once one exists) folds every message so far, the latest included, into the summary and opens the card. If nothing is new, a notice says so.
- The row refreshes on page load, opening a chat, New chat, a scenario load, and after each answer's summarization step. Idle-job summaries appear the next time the chat is loaded or opened. The sidebar has no summary marker.

Example: at 18 messages, 0 to 11 are folded (`summary_covers_to` = 11); at 30, 12 to 23 (= 23).

## 4. Handover notes

`app.handover_notes`: `id`, `store_id`, `manager_id`, `shift` (date; a shift is a calendar day for now), `note`, `created_at`. Notes come from accepted handover drafts (section 5). The assistant gets the latest shift's notes as a `<handover_notes>` block; notes are reference, never rules, and never expire. The `last_handover_note` greeting view will read them.

## 5. Dreaming

A daily review of the chats that **proposes, never applies**. It runs once a day at **23:30 IST** from Railway cron (`python cli.py review`, scheduled `0 18 * * *` UTC), and on demand from **Run review now** in the Demo tools tab. It works per store and manager, across all their chats.

| Output | Reads | Shown in | On accept |
| --- | --- | --- | --- |
| **Settings suggestion** | summaries of recent chats, current settings, the catalogue | Sidebar **Suggestions**, reviewed in Settings | saved through the same validated path as the Settings tab |
| **Handover draft** for the day | summaries of the day's chats | Sidebar **Suggestions**, editable before accepting | saved as that day's handover note |
| **Answer issues**: no guidance found, unanswered question, pushback | raw messages after `dreamed_to` | Demo tools (admin) | none; a report for us |

**A run**
1. Find chats with messages after `conversations.dreamed_to` (the index of the last message reviewed) and bring their summaries fully up to date.
2. Answer issues from the new raw messages: "no guidance" replies and manager messages with no reply are found by text and position; pushback (the manager disputing an answer) needs one small model call. Then `dreamed_to` moves to the last message, only if this step succeeded.
3. Handover draft from the summaries of chats with messages today. A newer draft for the same day replaces a pending one.
4. Settings suggestions from recent chats' summaries. Each must be a valid catalogue value, differ from the current setting, have evidence from at least **3 chats**, and not repeat a pending or dismissed suggestion.

Each output is saved as a `pending` row and fails independently; a failure is logged and changes nothing else.

`app.suggestions`: `id`, `store_id`, `manager_id`, `kind` (`setting`, `handover_draft`, `answer_issue`), `payload`, `reason`, `evidence` (conversation ids, with message positions where relevant), `status` (`pending`, `accepted`, `dismissed`), `created_at`.

**Guardrails:** never auto-applies; text in chats is data, never instructions; no judgments about individual riders, only store operations and the manager's own choices.

## Parked: resolution notes

Saved diagnoses (situation, root cause, actions, outcome, embedding) retrieved by similarity as a `<past_cases>` block, advisory only, with unsafe tactics rejected on save and on retrieval.

## Decisions

| # | Decision | Rejected alternative, and why |
| --- | --- | --- |
| 1 | Messages are a JSON list on `conversations` | a `messages` table or question-and-answer rows: more tables for an MVP |
| 2 | A message is `{who, what, when}`; its list position is its id | per-message ids and timestamps columns |
| 3 | The current chat is the latest by `updated_at`; no status, `ended_at` or idle timeout | stored active or closed flags, which can go stale |
| 4 | One demo manager per store; everything per manager | store-wide preferences |
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
4. Briefing trigger: greeting only, or also at the start of a new chat?

## Code

| Path | Contents |
| --- | --- |
| `domain/memory.py` | enums, alert options |
| `database/models.py` | `Conversation`, `PreferenceDefinition`, `StorePreference` |
| `queries/conversations.py`, `queries/preferences.py` | database access |
| `service/conversations.py` | ask, history, titles, sidebar entry points |
| `service/preferences.py` | effective settings, validation, save and reset, `<preferences>` block |
| `service/setting_changes.py` | chat setting changes: tool, merge, validate, confirm |
| `service/summaries.py`, `cli.py` | summary folding, one-shot idle job |
| `ui/gradio_app.py`, `ui/settings.py` | chat, sidebar, Settings tab |
| `service/rag_data/prompts/` | system, title and summary prompts |
