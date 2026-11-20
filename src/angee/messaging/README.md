# Messaging contracts

Record chatter and source conversations have different identity. Chatter folds
through core `MergeableMixin`: the retired thread redirects to its survivor,
while its messages, follower occurrences and history stay on their physical rows.
The current record owns read, post and moderation admission through the record
permissions its model declares on `ThreadedModelMixin`: `thread_read_access`
(default `read`), `thread_post_access` and `thread_moderate_access` (both default
`write`). A record whose `write` reaches people who must not edit, delete or trash
other people's comments names a narrower `thread_moderate_access`. A record's
chatter thread has no owner, so the record's creator keeps nothing of it, its
messages, parts or files after losing the record; the record lends its readers the
thread through `messaging/thread#chatter_read`. Messaging contributes comments,
its existing composer, and linked source conversations to the framework Activity
stream; provenance contributes record history independently through the same owner.

Source threads and messages remain channel occurrences. A native `same_as` pair
statement is the identity fact, and applying it folds the newer copy into the
older through core `MergeableMixin` as a retained redirect (`merged_into`): no
message, member, cursor or part moves, so reply, correction, delivery, unread
cursors and provider purge always address the physical occurrence. Readers
collapse copies through `canonical()`, which keeps showing a copy whose survivor
the reader cannot reach, so an account that sees only its own channel still sees
the message. Show source copies lists the survivor and every readable copy with
its channel and reply transport. The `messaging.source_identity` rule asserts an
RFC Message-ID match after the envelope commits (account-local chat/provider ids
are never compared); people confirm or keep copies separate through the same
statement, and a kept-separate decision splits the redirect and refuses a later
fold. The pair rule principal is granted read and write on threads and messages
like the parties statement sources.

An authoritative source audience correction uses
`Message.objects.correct_source_thread(message, channel=..., expected_thread=..., thread=...)`.
The native owner compares the locked previous thread, requires the envelope and
both threads to belong to that channel, and refuses locally authored messages.
It preserves content and repairs participants and both physical thread counters.
`ingest(..., explicit_thread=..., expected_thread=...)` composes the same command
with envelope ingestion; absent the exact precondition, changing an explicit
source thread remains refused. Historical ingestion suppresses live message
events while retaining native content history and counter updates.

`Message.objects.compose_reply(parent, *, body, sender, actor, ...)` writes a reply
on its parent's thread and channel under the channel's `reply` grant; the reply's
`parent` is its In-Reply-To. An email reply (the parent's kind is `email`) is a
complete envelope: `to` participants naming the parent's `Reply-To` addresses
when it carries any (`Message.reply_addresses`, the one reader of that header),
else the handle it was sent from (RFC 5322 §3.6.2); the
subject `Re: <thread title>` (the title is the normalized subject, so prefixes
never stack); and a Message-ID minted as Django mints one, stored bare as its
external id like a received email's. A parent with neither is refused. The
email transport renders that id as `Message-ID` and the parent's as `In-Reply-To`
and `References`, so an answer threads back onto the conversation through
`In-Reply-To`, through `References` when a provider rewrote the reply's
Message-ID, or through its subject; a retained header part never renders a second
copy of these. A reply of any other kind keeps a client token for its transport's
provider id and writes no envelope.

Outbound transport implements `ChannelBackend.deliver(message)` and returns the
two-field `DeliveryOutcome(accepted, provider_id=None)` from
[`backends.py`](backends.py). Acceptance settles the existing row to `sent` and
records the provider id. An identity collision retains the client id and records
`delivery_conflict`, while still settling `sent`. A declined outcome or legacy
`False` settles FAILED. Only messaging's own pre-send envelope validation is a
definite validation refusal; a transport-raised `ValidationError` is uncertain.
Backends raise integrate's `IntegrationError(transient=True, retry_after=…)` only
after definite non-acceptance. `DeliveryUncertain` represents a possible publish
whose acknowledgement was lost; unclassified post-send exceptions also settle
uncertain and never trigger a blind retry. A missing
`retry_after` uses exponential backoff. The fourth refused provider attempt fails
permanently. Lock contention defers work without consuming an attempt.

`Message.deliver()` composes `queue_message_delivery`; the task composes
`Message.objects.claim_delivery` and `record_delivery`. `Message.reserve_delivery`
locks the row, decides eligibility and sets a bounded delivery lease; the dispatch
publishes the reserved identifiers through the jobs owner's on-commit hook. The periodic
`release_held_messages` task releases due drafts/retries and recovers expired
unclaimed dispatch leases, with SQL eligibility, skip-locked claims and a savepoint per row. Retry
settlement retains the enqueue-owned lease through the next scheduled attempt;
terminal settlement clears it. Queued rows without a lease are never released
automatically, even if they carry a due schedule.

`claim_delivery` commits a `delivery_claim` effect marker under the row lock before
network I/O. Its lease exceeds both worker limits using `angee.jobs.timeouts`.
Settlement checks that marker; superseded claims cannot settle a later
attempt. An expired claimed lease becomes `uncertain`, retains its parent answer
reservation, and sets `delivery_possibly_accepted`. `ChannelBackend.reconciles_delivery()`
admits automatic recovery only when `deliver()` safely reconciles the prior effect
before repeating it. Otherwise an operator calls `message.acknowledge_delivery`
or `message.retry_delivery` with an actor and observed revision, using the same
`send_held` permission as held replies. Acknowledge records sent without network
I/O; retry explicitly accepts repeat-effect risk and also admits FAILED rows.
Both record `acknowledged_by`; retry resets delivery attempts. These verbs are
the console GraphQL mutations `acknowledge_message_delivery` and
`retry_message_delivery`, each requiring `id` and `expected_revision`.
Automatic lookup retains UNCERTAIN and its revision, backs off exponentially,
and stops for operator-only rows after five unsuccessful recovery attempts.
The possibly-accepted marker survives operator retry and transport construction
failures. Only SENT settlement or an authoritative absence lookup clears it.
Local trash cannot retract a possibly public reply: it retains answer evidence
and can still reconcile or be acknowledged. Explicit retry restores a trashed
attempt before queueing it. A live reconciliation lease refuses both operator
verbs with `DeliveryReconciliationBusy`; idle recovery backoff does not.
For UNCERTAIN rows, `scheduled_at` is the next recovery horizon. A broker lease
alongside it protects a pending task; a lease with no horizon protects a live
worker. Recovery settlement clears that lease and schedules the next horizon.
Delivery tasks are pinned to the stack's `celery` prefork queue because threads
workers cannot enforce the worker time limits on which the lease depends.
If sync has already ingested a reconciled provider identity in the same channel,
that provider row remains visible. `record_delivery` retains the local attempt
as a trashed SENT audit row, with `delivery_conflict` pointing to the provider id;
its idempotency key and provenance survive without a second visible row or count.
Advisory task locks reduce contention; the row claim is authoritative. This is
at-most-once recovery of an uncertain effect, not an exactly-once provider guarantee.

Configure outbound email through Django's native default mailer, for example
`MAILERS = {"default": {"BACKEND": "anymail.backends.mailgun.EmailBackend"}}`,
with the provider's `ANYMAIL` settings. A missing default mailer leaves delivery
unconfigured. The sender opens, sends through, and closes the same native backend;
it never contacts an implicit localhost SMTP transport.

Django 6.1 configuration cutoff: the host no longer consumes `EMAIL_URL` or
`EMAIL_BACKEND` environment inputs. Migrate deployment mail configuration and old
project `EMAIL_*` settings to the native `MAILERS` mapping in `settings.yaml`;
existing django-yamlconf overlays own secret values. No legacy adapter is retained.

```yaml
MAILERS:
  default:
    BACKEND: django.core.mail.backends.smtp.EmailBackend
    OPTIONS:
      host: smtp.example.test
      port: 587
      use_tls: true
```

Email uses the provider's definite refusal evidence: SMTP 4xx retries and SMTP
5xx fails; connection failure before SMTP sending retries. Once sending begins,
lost acknowledgements remain uncertain. For HTTP ESPs, Anymail-wrapped connect
timeouts and new-connection/DNS failures retry because no request was sent;
generic connection failures, read timeouts and HTTP 5xx remain uncertain. HTTP
4xx fails except 429, which retries. Uncertain mail requires operator resolution.

A **held draft** is an untrashed outbound draft, as defined by
`Message.held_draft_fields()`. A future `scheduled_at` holds delivery until due;
a draft with no release instant requires operator approval indefinitely.
`Message.objects.send_held_drafts(selection, *, actor)` and
`discard_held_drafts(selection, *, actor)` accept the GraphQL owner's
`ActionSelectionInput(id, expected_revision)` selection and return ordered
`ActionResult` outcomes. They authorize one bounded selection through channel
`write`, check every revision, and use `many_actions` for per-row refusals. The
channel's grantable `replier` relation grants `reply` together with comment read.
All untrashed messages count in their thread, including drafts, queues and failures.
Inserts use `F()` deltas; loss/restoration and settlement use a thread-scoped
aggregate with the same model predicate. Queueing never changes the count.

Moderation uses `trash_record` / `restore_record` and the native trash model.
Trashing an unsent outbound row cancels its delivery. Restoration never queues it
again. Generic message scalar updates are disabled; tags use the tags addon's
`tag` and `untag` mutations.
The channel's grantable `annotator` relation permits tag changes on readable
threads and messages. It grants no read, reply, content-edit or channel-management
access; pair it with `reader` when an automation needs to classify inbox content.
Trashed messages remain unavailable to annotators who cannot read them.
A message reader takes its sender address from the message's own envelope,
`Message.source_header_values`; `sender_name` names a sender only for readers of
its Handle. The Handle row, its tags and history stay parties directory data
under the Handle's own read; messaging contributes no Handle read arm.
If removal races with an already accepted provider publish, settlement records
`sent` while retaining trash. Restoring that row never publishes it again.

`ANGEE_MESSAGING_PROTECTED_LOCAL_KEYS` is contributed in
[`autoconfig.py`](autoconfig.py). Addons append their provenance keys there.
`Message.protected_local_keys()` owns the setting read. Ingest strips incoming
protected keys and retains their stored values, including on provider echoes;
echoes of locally authored delivery states preserve sender, direction and parts.
Synced own messages remain provider-owned: edits, sender updates and late-parent
repair land normally, and new own messages emit the live event. Trusted Python
callers contribute provenance at reply creation. Message metadata is never a
GraphQL write argument.

The single-message GraphQL `message_text(id)` projection reads native BODY fragments
and caps its result at 4 KiB of UTF-8, after the message read gate. List types carry
no body-text resolver. Console held actions use the filterable `is_held` projection,
capture displayed revisions and confirm publication. Every settled id is removed
from selection; retrying a row refusal selects a fresh row and revision. A transport
error retains selection. Selection-limit read errors explain disabled actions.

An email file (`message/rfc822`, the type IMAP keeps raw mail under) previews as its
message: the web fragment contributes the `messaging.email` provider to `PreviewPane`
through the manifest `previews`, with a `maxSize` of 10 MiB, so a larger file falls
through to the generic file fallback. postal-mime parses the file in the browser,
and the preview keeps only what it shows. The subject, in the message's
`Content-Language`, sits over `MessageEnvelope`, the sender, To/Cc/Bcc and date
header `MessageReader` also uses. The body toggles between the HTML and plain-text
alternatives when both exist, and the attachments are listed under it. The HTML
renders in `@angee/ui`'s `MessageHtmlFrame`, the sandbox `MessagePartsView` uses: its
inline images arrive as data URLs, and remote images stay blocked until the reader
loads them. HTML files keep the built-in source view.

Email delivery distinguishes definite refusals from uncertain acceptance. SMTP
4xx and provider rate limits retry; SMTP 5xx and permanent HTTP 4xx fail. SMTP
timeouts/disconnects after sending starts and ambiguous provider HTTP 5xx settle
uncertain, requiring acknowledgement before another send. DNS, TLS, connect
timeout and greeting disconnects during connection opening retry as not sent.

A channel asserts statements only through its protocol's declaration:
`ChannelBackend.source_kind` (default `None`, refused as a source) is what
`Channel.source_kind` returns, so an `Integration` principal resolves to it through
its capability child. Takeout and export backends declare `SourceKind.IMPORT` and
assert as the channel's `integration_ptr`, the same row that owns the takeout's
`SyncStream` and observed `RecordLink`s.

A channel held a public form in six columns of its own (`slug`, `is_published`,
`form_schema`, `form_schema_version`, `max_body_bytes`, `max_field_bytes`) until
web forms took forms over. Messaging no longer declares them, and each project
removes them in its own migration history: with its copy of the web forms
bridge's template where channels hold a form, which carries each form to a
`webforms.Form` first, and with an ordinary autodetected removal where none
does. [`webforms.py`](webforms.py) keeps what those retained migrations import:
`default_webform_schema`, the column names, and the one judgment of "this
channel holds a form" (`holds_form`, `channels_holding_forms`), which the
template uses to move and messaging uses to refuse.

Messaging refuses to let the columns go full
([`webform_columns.py`](webform_columns.py)). Its `pre_migrate` receiver stops a
`migrate` run, also under `provision`, whose plan removes one of them with a
migration that neither declares itself the move (`moves_webform_channels = True`
on its `Migration`) nor follows it, while a webform channel of that database
holds a form; nothing is applied, and the refusal names the migration, counts
the channels and gives the remedy. `check --database` says the same as
`angee.E054`. It reads `RemoveField` on the channel, also inside
`SeparateDatabaseAndState`; a column dropped by `RunSQL`, or renamed and then
removed, is not seen. Interim: deleted once the framework pins django-compose
0.1.0.dev14, whose drop guard judges pending removals at migrate time; the
module's docstring says what the bridge's template changes with that pin.
