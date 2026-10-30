# IMAP historical samples

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
