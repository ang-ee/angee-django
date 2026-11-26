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

Whom a message is addressed to is one concept at two levels. As addresses it is
the message's `Recipient` rows, one per handle and envelope role (`to`, `cc`,
`bcc`) beside its one sender, `Message.sender`; as accounts it is chatter's direct
addressees, `recipient_user_ids` when a comment is posted and
`suggested_recipients` when one is composed. A project whose migration history
still has messaging's `Participant` copies the template
`compose_migrations/messaging/participants_to_recipients.py`, which renames the
model with its rows; `CHANGELOG.md` gives the steps.

An authoritative source audience correction uses
`Message.objects.correct_source_thread(message, channel=..., expected_thread=..., thread=...)`.
The native owner compares the locked previous thread, requires the envelope and
both threads to belong to that channel, and refuses locally authored messages.
It preserves content and repairs recipients and both physical thread counters.
`ingest(..., explicit_thread=..., expected_thread=...)` composes the same command
with envelope ingestion; absent the exact precondition, changing an explicit
source thread remains refused. Historical ingestion suppresses live message
events while retaining native content history and counter updates.

An outgoing message is written by
[`MessageManager.compose`](managers.py) alone: its row, parts, recipients,
sender, thread and delivery hand-off. Callers pass intent and never write its
rows; hand-written outgoing rows are superseded. It runs as its actor and
decides before it claims or writes: who may answer a message is
`Message.require_reply`, whom and from where it is answered is
`Message.reply_envelope` (its refusal is `Message.reply_error`, which intake's
email route asks too), and posting on a record is the chatter's own gate,
`ThreadedModelMixin.require_post`. An email is sent from its channel's default
sender, and its text part is only its own text, never made from its HTML.
Each typed recipient is one entry, as
[`MessageManager.clean_recipient`](managers.py) reads it and as `compose` lands
it: a bare address or one explicit `Name <address>`. An entry a mail library
would read as another address or as several, or whose name or address a mail
header would not carry as read (a NUL byte, a control character, an
encoded-word opener: [`header_text`](email.py), the rule every subject and
display name the email backend writes takes), is refused (`invalid`) under its
role, never re-read. `compose` lands every address in lower case, as inbound
mail does, and finds its handle by that exact value: a contact another writer
stored with a capital is a handle row of its own, grouped with the lower-case
one by the handle owner's comparison key wherever a person is looked up; ending
that split is the handle owner's. A name inbound mail stored with a literal encoded word is
written on the wire as `header_text` carries it, so an answer to its sender
still reaches that sender. An address with an internationalised domain is
stored as typed and written on the wire with its domain in ASCII (IDNA) form;
one whose local part is outside ASCII is admitted but cannot be delivered, as
no transport here speaks SMTPUTF8: its delivery fails with that reason stored,
naming the address. A message takes at most `ANGEE_MESSAGING_MAX_RECIPIENTS`
addresses across `to`, `cc`
and `bcc` (50) and `ANGEE_MESSAGING_MAX_ATTACHMENTS` attachments (20), both
contributed in [`autoconfig.py`](autoconfig.py); more is refused (`too_many`)
under the argument that crosses the bound, before anything is read; an answer
taking more addresses than that from its parent's `Reply-To` is refused under
`reply_to`, and `Message.reply_error` says so. A
`scheduled_at` without an offset is refused (`naive_datetime`), never read in
the server's zone. The docstrings own the exact refusals and hold rules.

An email answer carries a Message-ID minted as Django mints one and renders
the parent's as `In-Reply-To` and `References`, so an answer arriving on the
channel it was sent from threads back onto its conversation. A message a person
starts opens a thread of its own; inbound mail joins it only through those
threading headers, never by its subject, which groups inbound mail with inbound
mail alone ([`ThreadManager.resolve`](managers.py)). An answer to a mail sent
from a record's chatter lands on that chatter as an email, not a comment, so it
is answered by email in turn.

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
channel's grantable `replier` relation grants `reply` (answering on the channel and
composing a new message there) together with comment read; `Channel.reply_permission`
is that permission's one name, which `Message.require_reply` and `compose` decide.
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

A channel held a public form in columns of its own until web forms took forms
over. [`webforms.py`](webforms.py) keeps `default_webform_schema` alone, the
default that `Channel.form_schema` named, because every initial messaging
migration authored while those columns existed imports it.

## Mailers

Messaging delivers every email through one mailer, `MAILERS["transport"]`:
Django's SMTP backend, or an Anymail ESP backend with its `ANYMAIL`/`ANYMAIL_*`
settings. Without one, delivery is not configured: nothing is sent, and a send
is refused (`OUTBOUND_EMAIL_NOT_CONFIGURED`). The sender opens, sends through,
and closes that mailer; it never contacts an implicit localhost SMTP transport.
Django's default mailer, `MAILERS["default"]`, is messaging's
[`SystemMailer`](email.py), contributed by [`autoconfig.py`](autoconfig.py): it
records each email Django sends (a password reset, `sendtestemail`, a link an
addon mails) on the system channel, and messaging delivers it through the
transport. A deployment states only the transport:

```yaml
MAILERS:
  transport:
    BACKEND: django.core.mail.backends.smtp.EmailBackend
    OPTIONS:
      host: smtp.example.test
      port: 587
      use_tls: true
```

Settings load refuses, naming the edit, a mailer setting that collides with the
one messaging owns: a deployment's own `MAILERS.default` (rename it to
`MAILERS.transport`, or remove it when a transport is stated), and a transport
that is the system mailer, which would deliver nothing. Django itself refuses its
legacy `EMAIL_*` settings beside `MAILERS`, which messaging always states: move
them into `MAILERS.transport` `OPTIONS`. The host no longer consumes `EMAIL_URL` or `EMAIL_BACKEND`
environment inputs; existing django-yamlconf overlays own secret values. One
limit belongs to the settings loader: a `MAILERS` written as a Python literal in
`settings.py` loses its raw marker once messaging contributes the default beside
it, so a `{...}` in one of its values is expanded as a settings reference;
`MAILERS` stated in `settings.yaml` or an overlay is unaffected.

| The deployment states | Result |
|---|---|
| nothing | the system mailer is the default; delivery is not configured |
| `transport` | the default is contributed; delivery is configured |
| `default: angee.messaging.email.SystemMailer`, with or without `transport` | accepted; configured exactly when `transport` is stated |
| its own `default` | refused: rename it to `transport` |
| its own `default` and `transport` | refused: remove `default` |
| `transport` as the system mailer | refused |
| a legacy `EMAIL_*` setting | refused by Django: move it into `MAILERS.transport` `OPTIONS` |
| `transport` as Django's locmem, console, file or dummy backend | starts; `check --deploy` reports `messaging.E004` |

Delivering a recorded message marks its context, and the system mailer refuses
to record while the mark is set: a transport that hands delivery back to Django's
default mailer (a subclass of the system mailer, or a wrapper that sends through
`mailers.default`) fails that delivery, and nothing is recorded again. The mark
closes re-entry in the delivering thread; a wrapper that hands off to another
thread or a queue is not seen by it, so the settings-load refusal is the early
instruction.

Error mail goes through the transport, never the system channel, which lives in
the database the error may be about. Messaging contributes `LOGGING` as Django's
`DEFAULT_LOGGING` with one change, the `mail_admins` handler naming
`using: transport` (with no transport, Django drops the report). It is contributed
raw, so the settings loader keeps the `django.server` formatter's `{server_time}`
as text. A host that states its own `LOGGING` keeps it, and Django applies its
`DEFAULT_LOGGING` first and the host's block over it: a block that does not name
the `django` logger keeps Django's own `mail_admins` there, which sends through
the system mailer. `messaging.W001` reports an `AdminEmailHandler` on any
configured logger that names no `using`, or the default. A host's own block
keeps Django's loggers and names the transport on the handler that reports
errors:

```yaml
LOGGING:raw: true
LOGGING:
  version: 1
  disable_existing_loggers: false
  filters:
    require_debug_false:
      (): django.utils.log.RequireDebugFalse
    require_debug_true:
      (): django.utils.log.RequireDebugTrue
  formatters:
    django.server:
      (): django.utils.log.ServerFormatter
      format: "[{server_time}] {message}"
      style: "{"
  handlers:
    console:
      level: INFO
      filters: [require_debug_true]
      class: logging.StreamHandler
    django.server:
      level: INFO
      class: logging.StreamHandler
      formatter: django.server
    mail_admins:
      level: ERROR
      filters: [require_debug_false]
      class: django.utils.log.AdminEmailHandler
      using: transport
  loggers:
    django:
      handlers: [console, mail_admins]
      level: INFO
    django.server:
      handlers: [django.server]
      level: INFO
      propagate: false
```

## System mail

The system channel is messaging's install row `messaging.system_channel`
(backend `system`, `SystemChannelBackend`; vendor `messaging.email`), loaded by
provision and recreated by the next load if deleted. It is ownerless and carries
no grant. `ChannelManager.system_channel()` is its one lookup, read as named
system work; absent, it refuses with `SystemChannelMissing`
(`SYSTEM_CHANNEL_MISSING`, under `channel`).

[`MessageManager.compose_as_system`](managers.py) writes system mail there:
one queued email per call, through `compose`'s own writer, in the caller's
transaction, from the deployment's `DEFAULT_FROM_EMAIL`
(`AnymailEmailChannelBackend.default_sender`; the channel names no sender of its own).
It takes a named `reason` and `to`, `cc`, `bcc`, `subject`, `text` and `html`,
never a channel. Its rows record the causing actor as `created_by`, which grants
nothing on messaging rows, and its conversation is recorded in the caller's
changeset, under the causing user. A handle it creates, the sender's or a
recipient's, has no creator: the handles are written under messaging's non-user
attribution (`messaging/system:outgoing`), so their history is a changeset of
its own beside the caller's, with the caller's change reason, naming no user.
Each call mints its own Message-ID.
`Channel.objects.system_mail_configured()` (`ChannelManager`) is the one
answer callers ask, from settings alone, whether system mail can be sent: the
backend the registry binds to the system key decides (a transport and a default
sender the sender rule accepts, for messaging's own), and `compose_as_system`
refuses exactly when it is false. `SystemChannelBackend.system_mail_configured()`
is that backend's own contract, which callers do not name. The sender rule is
`MessageManager.clean_recipient`, the one check every address messaging lands
takes, plus `carries_address`: a sender whose part before the `@` is outside
ASCII is refused, as no transport here speaks SMTPUTF8. A recipient's rule is
`Message.objects.clean_system_recipient(entry, key=...)`, which
`compose_as_system` applies to each recipient and a caller asks before offering
a send; both read nothing.

An error report hides every local of every frame of the system write, from the
system mailer and `compose_as_system` down to the ORM's and the SQL's, and of
the shared writer `_write_outgoing` down for any mail (`sensitive_variables`).
The delivery path is hidden the same way for any mail, as a message's string
form is its preview: `Message.deliver` down, the worker's
`run_message_delivery` down to the transport library's own frames, the release
task's `release_held_messages` down, and the operator's `retry_delivery` and
`acknowledge_delivery` down. Django's filter applies only with `DEBUG` off, so a
debug page shows them all, and it never filters an exception's own message: a
transport's or a database's error text can carry what the server echoed (an
address, a line of the mail). An ordinary `compose`'s report
therefore shows hidden frames below the writer, while `compose`'s own frame is
not marked. Frames above the system mailer are the caller's: Django's
`send_mail` holds the message it was given, so a caller that mails a credential
through Django's mail API marks its own frame with a bare `@sensitive_variables()`:
a named list still shows `send_mail`'s `message` and the wrapper's arguments. The delivery path logs a
message's id and an error's type, never the message, and the recorded
`delivery_error` keeps a transport's own text private. Nobody retries or
acknowledges a system mail whose delivery failed or is uncertain, as its sealed
`write` refuses both; the release task sends a lost one again. A failed or
uncertain system mail stays so: once its transient retries are spent, nothing
tries it again and no person can retry or acknowledge it; the feature that sent
it sends a new one.

The system mailer carries what a Django email can mean on the system channel:
`to`, `cc`, `bcc`, the subject, a plain or HTML body and one HTML alternative.
It drops `Message-ID` and `Date`, and does not carry `from_email`: system mail
goes from the default sender, which replaces `SERVER_EMAIL` on a direct
`mail_admins` too. It refuses attachments, `reply_to`, any other header, any
other body or alternative type, an `AnymailMessage`, an empty body (which Django
itself would send), and a recipient the transport cannot carry (`cannot_carry`,
under its role), so a mail that could never leave is never recorded as sent. A batch is recorded whole or not at all; built with
`fail_silently`, it logs a refusal and reports none sent.

The system channel holds system mail, live sign-in links among it,
so administrators alone read it, write, annotate or delete it, and nobody sends
on it as a person, administrators included: the framework writes it as named
system work (`MessageManager.compose_as_system`). These are messaging's
`permissions.zed`, the channel's system seal; `messaging.E003` refuses a
fragment arm on any of those permissions. Every other way a channel is read is
closed on it:

| Path | On the system channel |
|---|---|
| administrators | read, write, annotate, delete; never send (`reply`) |
| a channel `reader`, `replier` or `annotator` grant | grants nothing |
| an integration `reader` on its parent integration | lists the integration row, which holds no mail |
| a spaces team bound to the channel | grants nothing |
| a project bound to its integration | grants nothing |
| its integration's owner, after an administrator transfers it | grants nothing |
| a workflow trigger's principal | enabling a trigger on it is refused before any grant (`system_only`) |

A system message is trashed only through the shared trash verb, which decides
`delete` on it: administrators alone. A record's moderation never takes one off
(`Message.trash_error`): it sits in the system channel's own thread.

No verb shares a system thread or grants on a system message. Nobody shares a
system thread or writes a system message, administrators included, and none of
messaging's own arms writes a system thread, so the share verbs and a group or
project binding of the thread are refused; administrators keep their deletes.
What still reaches system mail without any verb: a `reader` or `editor` tuple
written directly and the source-identity rule's principal, which read it, and
another addon's arm on the thread's `read_granted`, `chatter_read` or `write`,
which holds whenever a system thread carries what that arm keys on (a group, a
project binding, a record's chatter edge), which only system code can give it.
Their reads are not subtracted because the inbox's planned reads cannot afford
it, and the composer appends the fragments' `write` arms after the seal.

The system channel is system-only (`ChannelBackend.system_only`): it is never
selected to send from (`ChannelQuerySet.outbound_email`) and never a trigger's
source. `compose` and `Message.require_reply` decide `reply` on the channel,
which nobody holds there, so `compose(channel=Channel.objects.system_channel())`
and an answer to a system message are refused for every person, administrators
included, with no branch of their own. On the system channel the selection and
the send agree: neither admits it. Its readers, administrators alone, find it
among channels as "System mail", its threads in the inbox and its messages in
timelines.
