# Messaging contracts

Record chatter and source conversations have different identity. Chatter folds
through core `MergeableMixin`: the retired thread redirects to its survivor,
while its messages, follower occurrences and history stay on their physical rows.
The current record owns read and post admission. Messaging contributes comments,
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
`Message.objects.claim_delivery` and `record_delivery`. Enqueue sets a bounded
delivery lease and publishes through the jobs owner's on-commit hook. The periodic
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
