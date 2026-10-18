# Public feed contracts

[`FeedBackend`](backends.py) is an integrate stream adapter. Source backends
implement `streams(*, deadline=None)` and `extract(stream, page_bound, *,
deadline=None)` using integrate's `StreamDefinition` and `StreamPage`. Integrate
owns durable `SyncStream` cursors, baseline completion, bounded page draining,
quarantine and reconciliation. Feeds inherit that driver; there is no feed-local
cursor or sync loop. Connection discovery composes integrate's
`discover_connection` and `apply_discovery` hooks. Feed adapters must discover
their identity; `FeedBackend.apply_discovery` writes it through the handle owner.

Posts supplies `record_key`, `apply_record` and `finish_page`. Each parsed post
lands through [`land_posts`](ingest.py), which composes messaging's one ingest
owner. Its overlay and provider trash state are applied before live events;
trashed rows never emit `message_ingested`. Cross-post relations finish after
successfully landed page records become visible. A source that unhides a row
restores only trash whose protected `provider_hidden` timestamp matches the current
trash stamp, preserving operator moderation. Pages land in transactional chunks.
`extract()` may include `RecordRefusal(external_id, code)` from
`angee.posts.backends` alongside `ParsedPost`. Use stable safe identities (a
deterministic fallback when parsing the id fails) and bounded semantic codes.
Refusals count toward `page_bound`. The base returns their identity from
`record_key()`, raises `SemanticError(code, details={"external_key": external_id})`
from `apply_record()` inside the page transaction, and excludes them from
`finish_page()` relation work. Integrate quarantines each identity independently
and commits successful records and the page cursor together. Providers need no
refusal dataclass or page-application overrides.

A feed is an IMPORT statement source (its channel backend declares
`SourceKind.IMPORT`): what an import lands from a feed's own history, such as a
contact its comments name, is that feed's assertion, ranked below a person's.

`Feed.live_since` is stamped once by `Feed.discovery_finished()` after integrate's
committed `discovery_finished(sender=type(bridge), instance=bridge)` signal. Each
record is historical when its sent timestamp is absent, precedes the horizon, or
the feed has no horizon yet. History and activity streams therefore classify
their records independently. Landing repairs
a bound, discovery-ready feed's missing horizon before classifying its next record,
using the completed discovery time when available. NULL never means everything live.

`MessagePublic.reply_to_comment(*, body, actor, local=None, idempotency_key=None)`
owns feed reply policy on the shared Message row. It composes
`reply_state()` under the comment's row lock on insertion, after replay, excluding
other runs and native platform answers. The same owner predicate serves workflow
eligibility; possibly accepted and SENT outbound deliveries still count after local
trash because trash does not retract a public reply. Other discarded and failed
replies do not. Reply preparation composes
`Message.objects.compose_reply` with the final hold state at insertion and native
idempotency replay. The actor needs readable context and channel `reply`.
Unreadable comments raise `PermissionDenied`. New attempts on answered comments
raise `CommentAnswered`; matching replays return their existing reply first.
`Feed.reply_hold` is operator-editable hours: null holds for approval indefinitely,
positive hours schedule a held draft, and zero queues immediate delivery. Replays
preserve the original hold and settlement. `local` is trusted Python provenance,
never an agent GraphQL argument. Retained replies advance thread counters on
insertion; queueing and idempotency replay never increment again. Replaying a
discarded receipt returns its row or an explicit validation refusal.

`FeedBackend.deliver` owns publication order and error classification. Providers
implement `publish_reply(message) -> str` and, to declare reconciliation capability,
`find_published_reply(message, *, since) -> str | None`. The publish hook makes one
provider call and returns its nonblank id; definite refusals raise native integrate
errors, and possible acceptance raises `DeliveryUncertain`. Only a persisted
possibly accepted settlement permits reconciliation; definite refusals retry directly.
The base resolves `Message.top_level_comment()` during context checks before any
quota debit or provider hook. A missing/invalid anchor returns
`DeliveryOutcome(accepted=False)` for a fresh send; it remains `DeliveryUncertain`
after possible acceptance. Providers still use that anchor owner in both hooks.
Declare `delivery_visibility_delay: ClassVar[timedelta]` on the backend to cover
provider read-after-write lag; the feed default is three minutes. Messaging sets
the first reconciliation horizon no earlier than
`delivery_started_at + delivery_visibility_delay`, then combines that lower bound
with subsequent exponential backoff. The base also guards direct delivery calls:
`find_published_reply()` is never consulted before the margin, and no lookup quota
is spent. Other channel backends default to a zero margin.
Recovery matches publisher, `Message.top_level_comment()` anchor and body, using
the claim time rounded down to seconds minus `RECONCILIATION_SKEW_MARGIN` (three
minutes), stopping at older replies. Another active local reply with the same
anchor and body in that window makes the result ambiguous and keeps it uncertain. Return
None only after an authoritative absence check; incomplete or unavailable searches
raise and cannot authorize another publish. Both provider hooks compose the shared
anchor owner. The Channel adapter uses the provider's classmethod capability and
holds integrate's bridge advisory lock through publication and settlement. Polling
and verified webhook landing compose the same lock.

An operator-typed Page reply by the same publisher, with the same anchor and body
inside this window, counts as the delivered reply. Provider evidence cannot
distinguish it from this delivery attempt. Unrelated replies with invalid parent
chains are ignored by the SQL ambiguity check; they cannot block another reply's
recovery. The part owner compares full plain bodies in their tree reading order.

Provider configuration extends `FeedBackend.Config`; the base owns the reply-reserve
validator and `policy`. Stream requests call `consume_quota(units, delivery=False)`.
`FeedBackend.deliver()` debits provider-declared `publish_reply_cost` and
`find_published_reply_cost` with `delivery=True` before each hook. Hooks perform
no quota debits or delivery-state writes, so the base is the one delivery quota owner.
The only permitted ledger-opening operation in a provider hook is
`current_quota_period(*, now=None)`: it delegates to `QuotaManager.open_period()`
and returns the current scope's period without a debit. Use `period_end` for
provider quota-error horizons. A reset between the base debit and the hook opens
the new period; it never relies on finding a previously debited row.
Providers declare `quota_scope()` with a namespaced true budget identity (an OAuth
client for project-wide quotas), and `quota_reset_timezone` with the reset IANA zone.
The shared `Quota` owner atomically enforces the current configured limit, leaving
the reply reserve for polling; local midnight periods honor DST. `quota_limit` is
read from the requesting feed for every check and never persisted onto the shared
row; the ledger's legacy `quota_limit` is historical data. Each debit opens the period
once; historical NULL-scope ledgers are never reopened. Retry-After parsing belongs
to `FeedBackend.retry_after(response)`.

Deleting the original integration authorization anchor preserves a shared scope's
ledger and usage; remaining spenders continue against that same budget.

Re-observation of the `active_thread_days` window (default 30 days) catches edits,
moderation changes, hides and deletes. Declare it as its own event-feed
`StreamDefinition`, with its own durable cursor, separate from live ingestion and
history. Before each re-observation request call
`consume_reobserve_quota(units, *, now=None) -> bool` with a positive API cost.
`FeedBackend.Config.reobserve_units_per_cycle` defaults to 100; zero disables
re-observation. The helper shares one allowance across the adapter's pages and
debits the daily polling quota, preserving `reply_reserve`. `False` spends neither
budget: end that stream's current drain, retaining its cursor (when no records
are pending, return `StreamPage((), stream.cursor, exhausted=True)`). Commit any
already fetched records and their cursor before stopping. Budget exhaustion is
normal completion of this cycle, so live ingestion continues. Integrate constructs
a fresh adapter for each sync cycle/parallel partition; never reset the allowance
per page or persist it in the scan cursor. Use one re-observation partition for
one allowance. An exhausted daily polling quota also returns `False` without
spending the cycle allowance; the live stream reports that exhaustion. A request
attempted after a successful debit spends its units even if the API fails. Other
reads continue using `consume_quota()`.

Feed HTTP calls compose
`request_json(method, url, *, is_transient, refusal, retry_delay=None, **kwargs)`.
It accepts GET/POST and forwards native `HttpClient.request` keyword arguments.
The result must be a JSON object. Transport errors, oversized responses, 5xx, a
success whose body is not a JSON object, and provider transient markers raise
`DeliveryUncertain` for POST and transient `IntegrationError` for GET. A 4xx whose
body is not a JSON object answered the request: both predicates run with empty
data, and it is never uncertain. `RequestNotSentError` (connect, DNS or TLS
failure before sending) propagates unchanged. The pinned transport's pre-connect
SSRF refusal (`AddressNotAllowedError`) becomes a permanent `IntegrationError`. A redirect
failure after an earlier request was sent retains integrate's uncertain transport
classification.

`is_transient(response, data) -> bool` detects provider markers for ambiguous
temporary failure; it runs before refusal handling. Then
`refusal(response, data) -> Exception | None` supplies provider-specific rate,
quota, authorization, cursor or record-local refusal. Definite temporary refusals
use `IntegrationError(transient=True, retry_after=...)`; permanent refusals use
`IntegrationError`, cursor failures use `CursorInvalid`, and record-local reads
may use `SemanticError`. Predicate exceptions propagate. An otherwise unhandled
HTTP 4xx is a permanent refusal. All exception messages/codes must be safe and
bounded. GET uncertainty uses `retry_delay(response)`, defaulting to
`self.retry_after(response)`; supply a callback or override `retry_after` to
combine provider usage headers with the shared Retry-After parser. Transport
failures have no response hint. The helper never spends quota: stream callers
debit before it, while provider delivery hooks rely on the base's declared-cost
debit.

The read tools are `read_comment_thread(sqid)` and `read_message_text(sqid)`, both
registered through the existing MCP GraphQL seam over actor-scoped
`messages_by_pk` and the single-message `message_text(id)` projection respectively.
Plain body projection belongs to messaging. `is_original_post`
is contributed through the model's `hasura_filterable_fields` declaration.

Feeds use integrate's `FORM` creation catalogue and existing add-integration page.
Backend implementation defaults supply vendor and platform identity. The saved
feed's `posts.Feed#actions` Connect child composes integrate's shared
`ConnectOAuthButton` with `CONNECT_RECORD_FIELDS` and its `ConnectIntegration`
document. It returns to that feed after OAuth and reloads the record.
