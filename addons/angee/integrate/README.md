# Integrations and record sync

An integration owns credentials, connection intent and permissions. A concrete
bridge adds scheduling, a dedicated `sync_run_id` dispatch pointer, and telemetry; its backend declares independently ordered
stream partitions. `Bridge.sync()` drives those declarations by default, while
existing capability overrides remain valid.

| Concern | Owner |
|---|---|
| Connection, cadence, queue admission and run telemetry | [Integration and Bridge](models.py) |
| Stream identity, opaque cursor, baseline generation and reconciliation policy | [SyncStream and its manager](models.py) |
| External identity, observed source, protected applied base and retained tombstones | [RecordLink and its manager](models.py) |
| Observed and applied snapshots | Native `RecordLink.history`, attributed by changeset |
| Inbound quarantine, outbox refusal and due retry policy | Link/stream state in [the shared state owner](mixins.py) |
| Bounded page, conditional push and inventory execution | [BridgeImpl](impl.py) and [stream driver](streams.py) |
| Concurrent nested JSON edits | [merge_json_state](models.py) |
| Operator inspection | Read-only record-sync resources in [the console schema](schema.py), inheriting [Integration permissions](permissions.zed); `manage.py preview_push <integration>` lists what a push would write ([`preview_push`](streams.py)) |
| Saved-record Streams tab, discrepancy/link drill-downs and cursor summary | [Generic Streams data views](web/src/IntegrationStreams.tsx), contributed once to Integration forms by [the web addon](web/src/index.tsx) |

Event feeds compose their domain's idempotent ingest verb and never create links
or revisions. Messaging uses conversation partitions for Slack and mailbox
partitions for IMAP. Their legacy bridge cursor slices seed the first stream row
only, through the pure `BridgeImpl.seed_cursor` hook. Legacy delivery policy
translates through `seed_config`, which returns config defaults and the retained
cursor with migrated policy removed. The driver fills missing `Bridge.config`
keys and saves that cursor under the bridge row lock in the same transaction as
stream seeding. Later partitions retain their positions without resurrecting
removed policy. Subsequent progress belongs to the stream. `Bridge.cursor` remains for
Mount's existing cursor cleanup and Feed's declared backend contract, as well as
the first-generation messaging seeds; its presence does not authorize a second
cursor writer for an adopted stream.

Record replicas use upstream `DeepDiff` to compare the pinned mapped projection
with the current local and remote mapped projections. The domain adapter supplies
both comparable shapes in `RecordChange`; source hashes use only
`canonical_json_sha256` and identify observations and write-back echoes.
Observation saves source evidence and never advances `applied_base`. Promotion
pins the link's own native history row written for that application. An unchanged pair
does nothing; a remote-only change applies locally; a local-only change can be
conditionally written back by a push-capable adapter. Divergent concurrent edits and
remote deletion versus a local edit remain open conflicts. Identical edits on both
sides are already converged. A write-back returns
the authoritative remote version and content hash; retaining those facts with
the local projection identifies its later echo without relying on timestamps.
The adapter owns locking and validating the local projection before applying it.
Adapter policy lives in `SyncStream.config`, seeded by `StreamDefinition` only
when the first epoch is created and preserved across epoch changes. A successful
conditional deletion returns `WriteBackResult(tombstone=True)` so the driver
retains the link's deletion and local origin. [Directory](../parties/README.md)
composes this protocol for bidirectional CardDAV contacts.

Before a two-way replica completes its first baseline, an adapter's identity
match between a live remote record and an existing local row with no applied link history
is adopted with remote wins.
The driver applies the remote projection and records its version and pins the applied
projection without a conflict or write-back. Each identity can be adopted across
baseline pages and cursor resets; retained application bases still use ordinary
three-way comparison. `push_stream` suppresses local pushes only until that first
baseline completes. Local-only rows can then be created remotely, including
during the same sync call that completes the baseline. Before upgrading pull-only
sync to two-way sync, remove any local-only rows that must not reach the remote
from the synchronized scope.

Changes to mapping version or dependency digest also require application. The
adapter returns applied evidence in `ApplyResult`; only the driver promotes the
primary link. `apply_record` returns one result for one record. An adapter promotion of that link aborts the page. Optional
`prepare_page` locks a page's compound identities and targets once, before the
record savepoints; it and the visibility hooks perform database work only.

The driver extracts outside a transaction, then commits each page's database
effects, inbound/outbox state and cursor together. Each record has a savepoint. A
declared semantic refusal, field validation failure or record-level `DataError`
quarantines that record; infrastructure failures roll back the page and propagate.
`finish_page` composes domain batch relationships
after successful rows are visible, before the cursor commits; messaging uses its
existing quotation owner here. External writes occur before their database
reflection and use the remote version precondition. Native JSON Patch `test`
preconditions fence reflection against changes to retained link state. If the process loses the
response, the next observation must reconcile that uncertainty; this protocol
does not promise an atomic commit across two systems.

Partition failures retain healthy partitions' committed pages. Both serial and
parallel syncs report each failed stream, partition and operation through the
shared safe failure projection, and log the original exception with its traceback.
The terminal progress details identify failures after all partitions finish, so a
healthy partition cannot replace them with its last page's progress.

Bounded callers resolve declarations through `open_stream`, use `begin_stream_cycle(stream, adapter)`
once, then `advance_stream` until its
result is exhausted, passing the returned stream after an epoch reset. The
caller closes its adapter. `push_stream` and `reconcile_stream` complete the
cycle when applicable. These functions contain no workflow runtime dependency.
`preview_push(stream, adapter)` returns the keys a push would write and those
held back, writing nothing. Before resuming an integration paused for a mapping
change, every stream should preview no writes.

The Streams tab keeps its drill-down in route search state. It reads failures
from stream/link state, offers explicit remote/local choices for conflicts, and
reads bounded observation/application history beneath the authorized link.

At cycle start, due non-conflict replica inbound failures are re-read
through `read_keys(stream, keys)` when the adapter declares
`supports_identity_reads`. It
returns one `RecordChange` for every requested external key, including a remote
tombstone when that key no longer exists. Transport runs outside transactions;
the shared page apply path commits the observations without changing the cursor,
phase or advancement timestamps. Successful apply resolves earlier non-conflict
failures for that identity. Event feeds are excluded from discrepancy rescan,
and so are refused writes (`remote_rejected`): the push retries those, since a
record the remote never stored has nothing to re-read.
An adapter without `supports_identity_reads` requests a baseline instead, recording the fallback
and reason in the discrepancy details. No private work queue is retained.
A pull that finds a record unchanged still retains the source it read, so a
remote edit the comparison hash cannot see is not reverted by the next write-back.

Semantic quarantine increments `attempts` and sets an exponential retry delay
starting at one minute, capped by the smaller of a positive reconciliation
interval or 24 hours. A zero interval means continuous inventory reconciliation,
so it retains the 24-hour retry cap. A refused write holds its link's write-back
until `retry_at`, on every write path (`RecordLink.write_holds`);
each refused link keeps its outbox state, and a later write that lands resolves it.
A full push (no `external_keys`) also closes a due refusal whose link has nothing
left to write, because its record was deleted or reverted locally: nothing would
retry it, and no rescan reads it.
Conflicts keep `retry_at=None` and require
explicit resolution before either side can be written again. `resolve_conflict`
requires `keep="remote"` (re-read and apply remotely owned facts) or `keep="local"`
(re-read the remote, retain its source as the base, then conditionally write the
local projection onto it).
Plain `resolve` refuses conflicts. A newer remote edit may reject the chosen
local write again and retain fresh outbox state.
An invalid/expired cursor or `resync_required` creates a new baseline generation,
carrying existing links and quarantine forward while retaining link history.

Inventory sweeps read and apply newly enumerated identities before incrementing
absence counts. Each `reconcile_stream(..., page_bound=100)` call commits one
bounded pulse; repeat while `SyncStream.reconcile_state` is nonempty.
Adapters implement `enumerate_keys(..., after=None)` as a stable
iterator with exclusive seek. The driver stores its checkpoint separately from
the adapter-owned opaque cursor.
`read_keys` handles unseen identities as well as existing links. Without that
operation, the driver first extracts a separate bounded baseline. Root and child
absence passes also checkpoint their progress; completion alone updates
`last_reconciled_at`. First absence records unavailability, then a retained
tombstone at the declared threshold. `on_absent` runs once per status transition;
`on_revalidated` runs after each successful unchanged observation. Both share the
status transaction so domain visibility changes roll back with the link.

Native link history retention is unbounded by design. Every observation retains
source evidence; every application retains source and mapped payloads; deployments must budget storage for
this growing history. No automatic pruning is performed.

A compound link declares one immutable root `parent` in the same stream. Children
follow parent absence and retries read the parent's identity; their own successful
evidence and discrepancy resolution remain with the aggregate adapter. Sweeps
never delete domain rows or overwrite an open conflict. A peer beyond its
tombstone retention period must reverify a baseline. Link observation and
promotion preserve an omitted target; explicit `target=None` clears its binding.

Inbound state retains several simultaneous failure identities and closed evidence.
Event failures without a link live on `SyncStream.inbound`, with native stream
history. A pinned link withholds automatic application and push; an explicit
remote conflict choice can apply it. Outbox refusals retain retry/error evidence,
and replaced or closed outbox states remain in link history.

The declared `integrate/fold_record_sync_history` migration converts the legacy
revision log in link/number order, preserves all failure identities and original
legacy audit stamps, and pins the last applied historical row. It does not invent
historical remote versions. Its reverse rebuilds the legacy tables and documents
the state distinctions their schema cannot express. The legacy models/tables
remain conversion inputs until their separate retirement; runtime readers and
writers use link/stream history and state.
