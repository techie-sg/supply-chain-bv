# Personalization through dreaming

DispatchDesk remembers personal context for each store manager: a name or email,
working preferences, and instructions the manager wants followed across chats.
It is a small editable context, not a catalogue of predefined response settings.

## When it updates

Chat reads saved context on every answer. It never writes personalization or
calls a personalization tool. Requests and personal details remain in the raw
conversation until the background review examines them.

The summary cron runs every five minutes and summarizes conversations idle for
at least ten minutes. After a summary, it reviews manager messages after that
conversation's `personalization_covers_to` marker, up to `summary_covers_to`.
The daily dreaming job and Demo tools' **Summary & personalization** action also
run this review. Failed or unfinished reviews are retried by the summary job.

Most batches return no update. The model saves context only when a manager
message states a durable personal detail or instruction. It excludes temporary
store conditions, questions, other people's details, quoted text, and secrets.
Existing context is preserved unless the manager explicitly corrects it or asks
to forget it. Assistant messages and summaries are not extraction evidence.

## Storage and limits

The existing `app.manager_personalization.preferences` JSON stores the context
under `additional_instructions`, with its source, timestamp, and message evidence.
No schema migration is needed. Legacy structured preferences remain readable.

Each model call receives up to 60 message positions and 24 KB of fresh text,
with a 48 KB total input budget. A run processes at most five batches per chat.
Saved context is limited to 4,000 characters. Every learned update must cite an
exact quote and message position from the current batch.

Context and the review marker save in one transaction. A concurrent context
edit rejects a stale update and leaves that batch pending for retry. Empty
results advance the marker without changing the context. Store and manager IDs
scope all reads and writes.

## Settings

**Settings → Personalization** shows the saved context and its supporting
conversation. The manager can edit or clear it directly. This manual edit is
separate from automatic dreaming and uses a snapshot check to avoid overwriting
a concurrent change.

Examples: “My name is Priya,” “My email is priya@example.com,” or “Keep answers
short for me.” Routine requests such as “How many orders are waiting?” should
produce no personalization update. Learning requires no approval button.
