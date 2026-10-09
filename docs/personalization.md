# Personalization

Personalization stores how each manager wants advice presented and which eligible
options to consider first. It is separate from incentive caps, alert thresholds,
batching rules, conversation summaries, handover notes and live store data.

Settings > Personalization provides answer length, answer order, comparisons,
decision priority and optional additional instructions. Preferences persist
across chats until explicitly changed. Select Use default or clear additional
instructions to remove them. Each saved item shows how it was saved and, when
available, links to its source conversation.

## Exactly when a saved profile changes

| Trigger | Requirement | Result |
| --- | --- | --- |
| Save personalization | A validated field actually differs from the loaded profile | Save only changed fields; stale edits are rejected |
| Latest chat message | A direct lasting request such as "Keep answers short for me", "Always keep your answers short", "I prefer detailed explanations", or "Forget my preference for short answers" | A turn-scoped tool validates the exact user quote and supported value, then saves or removes that field |
| Accept suggestion | A pending personalization suggestion still belongs to the manager and the field has never been set or removed | Apply the validated value and accept the proposal atomically |

Repeating a saved value changes nothing, including its source and save time.
No timer, page load, scenario load, summary or daily review applies a preference.
A removed field retains a null entry so old conversations cannot restore it.
An explicit new request or a Settings save can set that field again.

## Batched review only creates suggestions

Review runs after a **successful summary save**, on all existing summary paths:
Summarize Now, folding older messages after an answer, and the idle summary job
(chats idle at least 10 minutes when the job runs). Daily review also refreshes
summaries using this same path. The same cron command retries unfinished reviews
even when no new summary is needed. Manual summarization can also retry pending
review of an existing summary. There is no new scheduler.

1. Start after the conversation's own `personalization_covers_to`, and process
   only messages up to its saved `summary_covers_to`. Only qualifying **manager
   messages** are fresh evidence. Assistant text, earlier summaries and messages
   beyond summary coverage are excluded.
2. Apply conservative English request patterns. Routine dispatch questions,
   operational settings, facts, quoted examples, negations and temporary requests
   such as "make this answer shorter" or "for this shift" are ignored. With no
   eligible request, skip profile/evidence reads, extraction and preference writes,
   and mark the batch reviewed.
3. Exclude fields already saved or deliberately removed. Review never replaces
   them. If no unset field is supported by the new messages, stop.
4. A new explicit lasting request can create a proposal. A request without
   lasting intent requires the same preference in **three distinct chats**.
   Repetition within one chat does not count. Evidence lookup is bounded to the
   last 20 summarized chats and their last 60 messages, scoped to this manager.
5. Only then call the extractor. The usual output is `[]`. Each candidate must
   use an allowed code/value and exact user quotes at verified message positions.
   At least one supporting message must belong to the current review batch.
6. Save pending proposals and the batch's progress marker in one transaction.
   Duplicate, accepted or dismissed proposals for the same code/value are not
   generated again. A successful no-op also advances progress. Provider errors,
   malformed output and transaction failures leave that batch pending. Earlier
   successful batches remain complete. Summary and saved preferences stay intact.

Each batch covers at most 60 message positions, with at most 24 KB of fresh
serialized message text. The entire extraction input, including system prompt,
supporting messages and optional summary context, is capped at 48 KB of UTF-8
bytes. This is a conservative bound comfortably below the 132k-token context
limit, leaving room for output and provider formatting. Summary context is capped
at 2 KB; supporting evidence is included only when it fits. Evidence messages are
never silently truncated. A single qualifying request that exceeds the batch
budget remains pending and is logged instead of being sent over budget.

Each review processes at most five batches per chat. Remaining batches are picked
up by the next cron run. Progress saves use the expected previous marker so a
stale or concurrent worker cannot regress progress or duplicate a completed batch.

Example: "Why is the queue growing?" saves nothing. "Make this answer shorter"
applies to that answer only. "Keep answers short for me" can save a durable
preference through chat. If its chat tool did not save it, summary review can
propose it for approval; review never silently applies it.

## Storage and answering

Migration `0013_personalization` adds `app.manager_personalization`: one row per
manager with store scope and a small JSON object containing typed preferences,
source, quote, source conversation and save time. Personalization proposals use
the existing `app.suggestions` table. Writes serialize on the manager row;
acceptance and profile changes share a transaction.

Migration `0015_merge_personalization` joins this migration with the alert
migrations from `main`. Both existing upgrade paths converge on one head without
changing revisions that were already applied locally.

Migration `0017_personalization_progress`, directly after `0016_shifts`, adds
`personalization_covers_to` and `personalized_at` to each conversation, plus a
partial index for pending reviews.
The timestamp records the last successful batch, including no-ops. Position is
the processing boundary; cron-run timestamps do not define the message window.
Existing chats begin pending because a saved summary alone does not establish
that personalization review succeeded. Review never changes chat recency.

Every answer, including a new chat's first answer and replies in a resumed chat,
reads the selected manager's current profile once and supplies active values to
response generation. The profile is never pinned to an old conversation or
reused across managers. Personalization does not enter embeddings or retrieval queries.
The current request overrides default answer style; policy, validated operational
settings, approvals, live facts, citations and uncertainty requirements remain
binding. Free-text instructions can be saved only in Settings.

The previous weekly digest no longer runs or enters chat. Historical
`app.memory_digests` data is retained by the migration and is never automatically
converted into personalization.
