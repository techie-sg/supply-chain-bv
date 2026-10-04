# DispatchDesk memory design

Status: draft for review. Nothing here is implemented yet.

Scope: MVP only. Tables keep the fewest columns that make the behavior work. Columns deferred on purpose are listed under "Deferred".

Memory lets the assistant remember a store's operating knowledge across sessions and shifts. It has these parts, built in this order:

| Order | Memory | Type | Purpose |
| --- | --- | --- | --- |
| 1 | Conversation history | Episodic | Every question and answer stored; the foundation for the rest |
| 2 | Store preferences | Preference | Binding rules: alert threshold, batching constraints, incentive cap |
| 3 | Handover notes | Episodic | Short free-text notes passed between shifts |
| 4 | Conversation summary | Compressed | Columns on `conversations` that keep long chats within the model's context |
| Parked | Resolution notes | Episodic | Saved diagnoses (root cause, steps, outcome) for reuse |

Also planned but not designed yet: approval log, prospective memory (snoozed alerts, reminders), and trace events linked to messages.

Requirement covered: preferences stated in one session are recalled, unprompted, in a later session with the same manager (requirements.md section 4 and sample query 5).

## Tables at a glance

Four new tables in the `app` schema for the MVP, plus one parked. The conversation summary is two columns on `conversations`, not a table. Details are in the numbered sections below.

| # | Table | Purpose | Fields |
| --- | --- | --- | --- |
| 1 | `conversations` | One row per chat. Holds the chat's summary. | `id`, `store_id`, `manager_id`, `summary`, `summary_covers_to_id`, `created_at`, `updated_at`, `ended_at` |
| 2 | `messages` | One row per manager question and its answer. | `id`, `conversation_id`, `question`, `answer`, `status`, `question_at`, `answer_at` |
| 3 | `store_preferences` | Binding store rules: alert threshold, batching constraint, incentive cap. | `id`, `store_id`, `manager_id`, `kind`, `payload`, `status`, `created_at` |
| 4 | `handover_notes` | Free-text notes passed from one shift to the next. | `id`, `store_id`, `manager_id`, `shift`, `note`, `created_at` |
| Parked | `resolution_notes` | Saved diagnoses (root cause, steps, outcome) for reuse. | `id`, `store_id`, `title`, `situation`, `root_cause`, `actions_taken`, `outcome`, `embedding`, `created_at` |

## Principles

1. Preferences are typed data, not free text, so guardrails can check them in code.
2. Preferences are always loaded in full. They are few, so no vector search.
3. Preference rows are never edited in place. A change supersedes the old row, which keeps history.
4. A preference can make policy stricter, never looser.
5. Memory text is user-written data. The assistant treats it as reference, never as instructions.
6. Everything is scoped to a store.
7. Raw transcripts are not memory. The assistant recalls preferences and the last handover, not old chats.

## 1. Conversation history

Every manager question and assistant answer is stored in PostgreSQL. A **conversation** is one chat. It has many **messages**, and a message is one manager question together with the assistant's answer. Each conversation has at most one summary (section 5).

Today history exists only in the Gradio session and is lost on refresh, restart, **Clear chat**, or loading a scenario. Logs hold metadata only.

Other pieces depend on it: preference extraction, the summary, the trace (which will link to each message), the approval log, Week 4 evals, and demo transcripts.

### Why a message is a question and answer pair

It matches how the app runs: one question triggers one retrieval, one model call and one trace.

- One-to-one link with the trace, so a single `trace_id` column can be added when traces are built.
- A failed answer leaves one `failed` message, not an orphan user message to filter out.
- History is simple: order by `id` and expand each message into a user and assistant message for the model.
- It is the unit evals (question to expected answer) and the approval log need.

Handled elsewhere:

| Case | Where it goes |
| --- | --- |
| Tool calls and retrieval inside a message | Trace events |
| Manager approve or decline clicks | Action records linked to the message |
| Assistant-initiated messages (proactive alerts) | Out of MVP scope |

### Tables

`app.conversations`, one row per chat:

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid | primary key |
| `store_id` | text | scope |
| `manager_id` | text | the demo manager (Karthik) |
| `summary` | text, nullable | see section 5 |
| `summary_covers_to_id` | bigint, nullable | id of the last message included in the summary |
| `created_at` | timestamptz | when the chat started |
| `updated_at` | timestamptz | changes on every new message |
| `ended_at` | timestamptz, nullable | set when a newer conversation starts; null for the current one |

`app.messages`, one row per question and answer:

| Column | Type | Notes |
| --- | --- | --- |
| `id` | bigint | identity (auto-increment) primary key; its order is the message order |
| `conversation_id` | uuid | foreign key, cascade delete |
| `question` | text | the manager's question |
| `answer` | text, nullable | null while pending or if failed |
| `status` | text | `pending`, `ok` or `failed` |
| `question_at` | timestamptz | when the question was received |
| `answer_at` | timestamptz, nullable | when the answer or failure was recorded; null while pending |

### When a conversation starts and ends

A new conversation starts when:
- No conversation exists yet for the store and manager.
- The manager clicks **Clear chat**.
- A scenario is loaded.

A browser refresh or app restart does not start one; the latest conversation resumes.

There is no idle timeout and no status column. The current conversation is the one most recently updated for the store and manager. When a new conversation starts, the previous one gets its `ended_at`. Nothing depends on `ended_at` to find the current chat, so a crash cannot leave a stale "active" conversation.

If the same manager has two browser tabs open, both write to the latest conversation and their messages interleave. This is accepted for the demo.

### Write path

1. If there is no current conversation, or a start trigger just happened, create one.
2. Insert the message with the question, `question_at` and status `pending` before calling the model, so a provider failure never loses the manager's text.
3. Call the assistant.
4. Update the message with the answer and status `ok`, or status `failed`, and set `answer_at`. The error is logged, not stored. Never store stack traces or raw provider responses.
5. Update the conversation's `updated_at`.

### Read path

- On page load, restore the latest conversation for the store and show its messages. A refresh or app restart resumes the chat.
- Model history comes from stored data, replacing the Gradio state as the source of truth: the summary if one exists, followed by the `ok` messages with `id > summary_covers_to_id`, in `id` order.
- Listing past chats is a plain query on `conversations`.

### Privacy and retention

- Stored only in the project's own PostgreSQL database.
- Messages may contain rider names and operational details, so retention must match the PR/FAQ data-handling claims.
- Provide a delete-conversation action. It cascades to the conversation's messages. No automatic expiry for now.
- Never store API keys.

## 2. Store preferences

Table `app.store_preferences`:

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid | primary key |
| `store_id` | text | scope |
| `manager_id` | text | who set it |
| `kind` | text | `alert_threshold`, `batching_constraint`, `incentive_cap` |
| `payload` | jsonb | validated per kind (below) |
| `status` | text | `active` or `superseded` |
| `created_at` | timestamptz | |

Payloads:

| Kind | Payload example |
| --- | --- |
| `alert_threshold` | `{"metric": "pending_orders_per_available_rider", "operator": ">", "value": 2, "window": {"days": ["sat", "sun"], "start": "19:00", "end": null}}` |
| `batching_constraint` | `{"rule": "never_batch", "item_class": "frozen", "with": "any"}` |
| `incentive_cap` | `{"amount": 150, "currency": "INR", "per": "shift"}` |

Times use Asia/Kolkata. A null `end` means until close; the store closing time is still undefined (see corpus README).

Rules:
- Only one active row per store, kind and logical key (the metric for thresholds, the item class for batching, one for the cap).
- Saving a new value for the same key marks the old row `superseded`, after the manager confirms.

### Validation against policy

Policy floors are code constants kept in sync with the corpus.

| Kind | Allowed |
| --- | --- |
| `alert_threshold` | Value at or below the policy default (about 2 pending orders per available rider) |
| `batching_constraint` | Rules that add restrictions only; cannot allow frozen items to be batched |
| `incentive_cap` | Any positive amount; no policy cap exists yet |

A looser preference is rejected and explained to the manager.

### Write path

1. The manager writes something like "Remember: weekends after 7pm, alert above 2 orders per rider."
2. An LLM call extracts it into the payload model using structured output.
3. Code validates it, including the policy check.
4. If it replaces an active preference, ask the manager to confirm.
5. Save it and reply with exactly what was stored. The manager can undo it.

If extraction fails or the request is ambiguous, ask a clarifying question and save nothing.

### Conflicts

| Case | Handling |
| --- | --- |
| New preference vs active preference | Ask the manager to replace or keep |
| New preference vs policy | Reject, explain the policy limit |
| Proposed action vs stored preference | Surface the conflict; do not silently override. A code-level check arrives with the Week 3 guardrails |

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

At the start of every question, load the active preferences and the last shift's handover notes for the store. Add them to the prompt as a `<preferences>` block and a `<handover_notes>` block. The system prompt already allows for stored preferences (Sources of truth, item 3).

## 5. Conversation summary

Each conversation has at most one summary, stored on its `conversations` row (`summary` and `summary_covers_to_id`). There is no separate summaries table.

Purpose: keep long chats within the model's context. Full history is passed on every question today, which gets slow and expensive in a long peak and eventually overflows. The summary replaces older messages.

How it works:
- When the history grows past a limit, the older messages are summarized, together with the previous summary if there is one.
- The result is saved in `summary`, and `summary_covers_to_id` records the last message it covers. The same row is updated in place; old summaries are not kept.
- The prompt is the summary plus every message after `summary_covers_to_id`, word for word.

Principles:
- The messages are the source of truth. A summary can always be discarded and regenerated.
- Preferences are loaded separately every question, so summarizing never drops a stored rule.
- The summarizer must not add facts. Figures in a summary are historical and labeled with their time.
- Advisory only. A summary never overrides policy, preferences or tool data.
- Because it lives on the conversation, a restart or refresh keeps it, with no repeated model call.

A handover note drafted from a chat is generated on request and saved by the manager as a `handover_notes` row. It is not a separate stored summary.

Open question: is the limit a token budget or a message count?

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
| `backend/database/models.py` | `Conversation`, `Message`, `StorePreference`, `HandoverNote` |
| `backend/alembic/versions/` | One migration per phase |
| `backend/domain/memory.py` | Preference payload models |
| `backend/queries/conversations.py` | Start conversation, add message, complete message, load latest, update summary, delete |
| `backend/queries/preferences.py` | Database access for preferences and handover notes |
| `backend/service/conversations.py` | Orchestration around the answer call |
| `backend/service/memory.py` | Extraction, validation, conflict logic |
| `backend/ui/gradio_app.py` | Restore on load, save on each question |
| `backend/tests/` | Tests for each of the above |

## Testing

Conversations:
- A conversation survives a page refresh and an app restart.
- A failed model call keeps the question and records a `failed` message.
- Loading a scenario or Clear chat starts a new conversation and keeps the old one stored.
- Message order (`id`) is stable; failed and pending messages are excluded from model history.
- With a summary present, the prompt is the summary plus only the messages after `summary_covers_to_id`.

Preferences:
- A preference stored in session 1 is applied in a new session without being repeated.
- A conflicting request is flagged instead of overridden.
- Record both transcripts as task 17 evidence.

## Decisions

1. Manager identity: a single demo manager (Karthik) per store, recorded in `manager_id` on conversations, preferences and handover notes. Preferences and notes are scoped to the scenario's `store_id`, so a new browser session or restart still recalls them.
2. Policy floors: code constants holding default values. The values are decided during implementation.
3. Handover notes: only the last shift's notes are given to the assistant. Old notes are kept and never expire.
4. Resolution notes: parked for later. Similarity threshold and top-k are decided when that phase starts.
5. A conversation has many messages (a message is one question and answer pair) and at most one summary, held as columns on `conversations`. No separate summaries table.
6. A new conversation starts on first use, Clear chat, or a scenario load. No idle timeout and no status column; the latest by `updated_at` is the current one, and the previous one gets `ended_at`.
7. MVP columns only. Deferred columns are listed below.

## Deferred

Left out of the MVP on purpose, to add when needed:

| Table | Deferred columns | Needed for |
| --- | --- | --- |
| `conversations` | `scenario_key` | Evals and reporting by scenario |
| `messages` | `source`, `trace_id`, `meta` | Proactive alerts, trace events, token stats |
| `store_preferences` | `superseded_by`, `source_text`, `message_id` | Audit trail |

## Open decisions

1. Include a UI to browse past conversations, or storage only for now?
2. Conversation retention: keep forever for now, or set a limit?
3. Summary limit: token budget or message count?
