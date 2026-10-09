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
| Latest chat message | A direct lasting request such as "Always keep your answers short", "I prefer detailed explanations", or "Forget my preference for short answers" | A turn-scoped tool validates the exact user quote and supported value, then saves or removes that field |
| Accept suggestion | A pending personalization suggestion still belongs to the manager and the field has never been set or removed | Apply the validated value and accept the proposal atomically |

Repeating a saved value changes nothing, including its source and save time.
No timer, page load, scenario load, summary or daily review applies a preference.
A removed field retains a null entry so old conversations cannot restore it.
An explicit new request or a Settings save can set that field again.

## Summary review only creates suggestions

Review runs after a **successful summary save**, on all existing summary paths:
Summarize Now, folding older messages after an answer, and the idle summary job
(chats idle at least 15 minutes when the job runs). Daily review also refreshes
summaries using this same path. A skipped, unchanged or failed summary triggers
no preference review. There is no new scheduler.

1. Inspect only newly folded **manager messages**, from the previous summary
   position through the newly saved position. Assistant text, earlier summaries
   and the recent messages excluded from a fold are not new evidence.
2. Apply conservative English request patterns. Routine dispatch questions,
   operational settings, facts, quoted examples, negations and temporary requests
   such as "make this answer shorter" or "for this shift" are ignored. With no
   eligible request, skip profile/evidence reads, extraction and writes.
3. Exclude fields already saved or deliberately removed. Review never replaces
   them. If no unset field is supported by the new messages, stop.
4. A new explicit lasting request can create a proposal. A request without
   lasting intent requires the same preference in **three distinct chats**.
   Repetition within one chat does not count. Evidence lookup is bounded to the
   last 20 summarized chats and their last 60 messages, scoped to this manager.
5. Only then call the extractor. The usual output is `[]`. Each candidate must
   use an allowed code/value and exact user quotes at verified message positions.
   At least one supporting message must belong to the new summary slice.
6. Save a pending proposal only. Duplicate, accepted or dismissed proposals for
   the same code/value are not generated again. Extraction failure leaves the
   summary and saved preferences intact.

Example: "Why is the queue growing?" saves nothing. "Make this answer shorter"
applies to that answer only. "Always keep your answers short" can save a durable
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
