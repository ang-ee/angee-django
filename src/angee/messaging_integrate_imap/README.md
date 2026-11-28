# IMAP historical imports

## Mbox files

Import a plain mbox archive through **Files → select the file → Run workflow →
Archive import from file**. The review offers writable, **paused IMAP channels**;
choose the channel whose history should receive the messages. Resume the channel
when the workflow finishes. The IMAP addon installs the shared archive workflow
through its `workflows_integrate` dependency.

If no writable IMAP channel is paused, **Confirm archive targets** fails with an
instruction to pause one. Pause the intended channel and retry that step to open
the destination selection; nothing is imported before you confirm a destination.

The source can be an uploaded Storage File or a file already on disk. For a large
archive, connect a local-folder mount in **Files → Mounts**, use reference mode,
and sync the mount to index the file before starting its workflow. Keep the file
unchanged during import. Local files are read in place; other Storage backends
use a temporary local copy for each attempt and need enough free disk space.
The importer verifies the Storage content hash; changed files must be re-indexed
and imported in a new run.

The [extractor](extractor.py) uses Python's `mailbox.mbox` reader and the same MIME
parser as live IMAP. It retains attachments, message dates, sender direction and
reply links. It imports historical records without emitting `message_ingested`
or advancing live IMAP cursors, and never connects to the mail server. Existing
messages with the same Message-ID are preserved; an identical archive replay can
repair late parent/thread links. ID-less messages use a raw-byte digest, so an
export that changes their bytes cannot deduplicate against the live copy.

Native workflow checkpoints bound each import page to 25 messages or 64 MiB of
raw mail. A single message over 64 MiB fails explicitly. Retrying a failed item
replays its last unfinished page safely; cancellation stops at the next heartbeat.
Reconciliation passes link replies even in reverse-order exports. The completion
result reports archive entries and passes, not a count of newly created messages.
The native mbox reader scans its table of contents for each page; remote sources
also require another temporary copy, so local reference mounts suit large files.
ZIP exports and Apple Mail directory bundles must first be extracted to their
plain mbox files. Mbox `From ` separator escaping follows the standard library's
mboxo reader.

## Mailbox samples

A paused IMAP channel can preview mailbox headers and import an explicitly
selected historical sample without moving its live polling cursor. Preview
opens the mailbox read-only and uses `BODY.PEEK[HEADER]`; import also uses
read-only access and reports whether mailbox flags changed during the operation.
Channel admission and import policy live on the [channel donor](models.py).

The console exposes `preview_imap_sample` as a paged GraphQL **query**;
`import_imap_sample` remains a mutation. Choose a date window or all dates,
preview the newest matching messages, then load older pages. Loading older
messages retains selection; changing any preview input or explicitly starting
a fresh preview clears it. An import accepts at most 50 selected messages.

The [backend preview owner](backend.py) pins the mailbox UID identity and an
upper UID boundary. Searches stay below that boundary and, on continuation,
below the previous page. New arrivals cannot enter the selection. A changed
UID identity or unavailable snapshot requires a fresh preview.

The first search computes the matching count once. Each continuation carries
the preceding count and subtracts only UIDs whose disappearance is confirmed
between that page's search and header fetch. Expunges elsewhere are not
rescanned, so the banner's snapshot count is not a current mailbox total.
An unanswered fetch for a UID still present fails instead of silently advancing.
The frozen `ImapSamplePreviewRequest` carries this contract from the flat
GraphQL arguments through the model to the backend.

The [web action](web/src/ImportImapSampleAction.tsx) composes `DialogForm` fields
with React Hook Form for values, validation, and submission. Only a valid submitted
request reaches `useAuthoredKeysetFeed`; incomplete drafts never become mailbox
requests. Form values normalize mailbox names once; validation messages are linked
to their invalid inputs, including both ends of an invalid date window. Invalid
page sizes show a validation error instead of being clamped.
The operations expose no sample-limit metadata, so the exported
`IMAP_SAMPLE_LIMIT` is the frontend's one bound, checked against the backend owner.
Native Query pages retain loaded headers, mailbox
identity, and the snapshot count, including when a page is empty. The dialog
reads those page facts; continuation cursors stay private to the feed adapter. The preview opts out of automatic refetches and
retries; mailbox probes follow explicit preview and load-older actions. A fresh
preview resets the query's pages through the shared owner.

Mailbox polling stores each mailbox position in its `SyncStream.cursor`.
Future-only policy belongs to the bridge's configuration. The paused
starting-point action snapshots remote boundaries before its transaction and
installs those positions through the driver's stream reset owner; epoch resets
preserve current configuration. Retired bridge cursor formats are not read by
the live backend.

Polling admits IMAP commands and reconnect retries only within the driver's
extraction deadline, using IMAPClient's native connection and socket timeouts.
If a page runs out of budget, its records are discarded and its durable cursor
stays unchanged; the next attempt rebuilds the UID plan from that position.
Expired sessions shut down locally instead of waiting for a LOGOUT response.
A message the parser fails on, for any reason, still lands through the
raw-message fallback, so one poison message never stalls its mailbox; worker
cancellation propagates to the task owner with the cursor unchanged.

The deadline bounds transport work between commands; it does not interrupt an
atomic page application. Configure the existing `sync_page_bound` and
`sync_time_budget` on slow channels to leave time for page application and task
settlement below the worker's limit.
