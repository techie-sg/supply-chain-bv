# DispatchDesk memory design

Status: draft for review. Nothing here is implemented yet.

Memory lets the assistant remember a store's operating knowledge across shifts. It has three parts, built in this order:

| Phase | Memory | Purpose |
| --- | --- | --- |
| 1 | Store preferences | Binding rules: alert threshold, batching constraints, incentive cap |
| 1 | Handover notes | Short free-text notes passed between shifts |
| 2 (parked) | Resolution notes | Saved diagnoses (root cause, steps, outcome) for reuse in similar situations |

Requirement covered: preferences stated in one session are recalled, unprompted, in a later session with the same manager (requirements.md section 4 and sample query 5).

## Principles

1. Preferences are typed data, not free text, so guardrails can check them in code.
2. Preferences are always loaded in full. They are few, so no vector search.
3. Rows are never edited in place. A change supersedes the old row, which keeps history.
4. A preference can make policy stricter, never looser.
5. Memory text is user-written data. The assistant treats it as reference, never as instructions.
6. Everything is scoped to a store.

## Phase 1: Store preferences

Table `app.store_preferences`:

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid | primary key |
| `store_id` | text | scope |
| `manager_id` | text | who set it |
| `kind` | text | `alert_threshold`, `batching_constraint`, `incentive_cap` |
| `payload` | jsonb | validated per kind (below) |
| `status` | text | `active` or `superseded` |
| `superseded_by` | uuid, nullable | the row that replaced this one |
| `source_text` | text | the manager's original words |
| `session_id` | text | session that created it |
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
- Saving a new value for the same key supersedes the old one, after the manager confirms.

### Validation against policy

Policy floors are code constants kept in sync with the corpus.

| Kind | Allowed |
| --- | --- |
| `alert_threshold` | Value at or below the policy default (about 2 pending orders per available rider) |
| `batching_constraint` | Rules that add restrictions only; cannot allow frozen items to be batched |
| `incentive_cap` | Any positive amount; no policy cap exists yet |

A looser preference is rejected and explained to the manager.

## Phase 1: Handover notes

Table `app.handover_notes`: `id`, `store_id`, `shift_date`, `shift_label`, `note`, `author`, `created_at`.

- Free text, short.
- Only the most recent shift's notes for the store are shown to the assistant as reference data. Older notes stay in the table and never expire.
- Notes are not rules and never override policy or preferences.

## Write path

1. The manager writes something like "Remember: weekends after 7pm, alert above 2 orders per rider."
2. An LLM call extracts it into the payload model using structured output.
3. Code validates it, including the policy check.
4. If it replaces an active preference, ask the manager to confirm.
5. Save it and reply with exactly what was stored. The manager can undo it.

If extraction fails or the request is ambiguous, ask a clarifying question and save nothing.

## Read path

At the start of every turn, load the active preferences and the last shift's handover notes for the store. Add them to the prompt as a `<preferences>` block and a `<handover_notes>` block. The system prompt already allows for stored preferences (Sources of truth, item 3).

## Conflicts

| Case | Handling |
| --- | --- |
| New preference vs active preference | Ask the manager to replace or keep |
| New preference vs policy | Reject, explain the policy limit |
| Proposed action vs stored preference | Surface the conflict; do not silently override. A code-level check arrives with the Week 3 guardrails |

## Phase 2: Resolution notes (parked)

Not part of the current build. Kept here so the design is not lost.

Table `app.resolution_notes`, scoped per store:

| Column | Notes |
| --- | --- |
| `id`, `store_id`, `manager_id` | |
| `title` | short label |
| `situation` | symptoms and conditions |
| `root_cause` | the RCA |
| `actions_taken` | ordered steps |
| `outcome` | `worked`, `partly_worked`, `did_not_work`, plus a short note |
| `occurred_on` | date; figures in the note are historical |
| `embedding` | vector of situation, root cause and actions |
| `status` | `active` or `archived` |
| `created_at`, `last_used_at` | |

Write: the manager asks to save a diagnosis, the LLM drafts the note from the conversation, the manager edits or approves, code runs the safety check, then it is saved and embedded.

Read: embed the question, search active notes for the store, keep matches above a similarity threshold, and add them as a separate `<past_cases>` block. These are kept out of `document_chunks` because they differ in scope, mutability and trust.

Guardrails:
- Reject notes that describe unsafe tactics (speeding, skipping breaks, over-hours work, penalties). Run the same check when notes are retrieved.
- The assistant presents notes as "your note from <date>", advisory only. Policy and guardrails always win.
- Figures in a note are history, labeled with `occurred_on`, never live data.
- Managers can edit, archive and delete notes.

## Code layout

| Path | Contents |
| --- | --- |
| `backend/database/models.py` | New models |
| `backend/alembic/versions/0005_*.py` | Migration |
| `backend/domain/memory.py` | Payload models |
| `backend/queries/preferences.py` | Database access |
| `backend/service/memory.py` | Extraction, validation, conflict logic |
| `backend/tests/` | Tests for each of the above |

## Testing

Phase 1 passes when a preference stored in session 1 is applied in a new session without being repeated, and a conflicting request is flagged instead of overridden. Record both transcripts as task 17 evidence.

## Decisions

1. Manager identity: fixed demo manager (Karthik) scoped to the scenario's `store_id`, so a new browser session or restart still recalls preferences.
2. Policy floors: code constants holding default values. The values are decided during implementation.
3. Handover notes: only the last shift's notes are given to the assistant. Old notes are kept and never expire.
4. Resolution notes: parked for later. Similarity threshold and top-k are decided when that phase starts.
