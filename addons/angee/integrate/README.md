# Integrations and record sync

An integration owns credentials, connection intent and permissions. A concrete
bridge adds scheduling and telemetry; its backend declares independently ordered
stream partitions. `Bridge.sync()` drives those declarations by default, while
existing capability overrides remain valid.

An installed execution composition may select durable dispatch through
`Bridge.dispatch_sync()`. A declared `sync_workflow_key` uses
[`workflows_integrate`](../workflows_integrate/README.md); `SyncDispatch.DISPATCHED`
defers terminal telemetry to that owner. Direct bridges still return an integer.

| Concern | Owner |
|---|---|
| Connection, cadence, queue admission and run telemetry | [Integration and Bridge](models.py) |
| Stream identity, opaque cursor, baseline generation and reconciliation policy | [SyncStream and its manager](records.py) |
| External identity, last-synced remote/local hashes and retained tombstones | [RecordLink and its manager](records.py) |
| Immutable observed and applied evidence | [RecordRevision and its manager](records.py) |
| Record quarantine and due rescan candidates | [SyncDiscrepancy and its manager](records.py) |
| Bounded page, conditional push and inventory execution | [StreamAdapter and driver](streams.py) |
| Source/local field declarations, immutable provenance and explicit imports | [ExternalOwnershipMixin and ExternalOwnershipManager](ownership.py) |
| Concurrent nested JSON edits | [merge_json_state](models.py) |
| Operator inspection | Read-only record-sync resources in [the console schema](schema.py), inheriting [Integration permissions](permissions.zed) |
| Saved-record Streams tab, discrepancy/link drill-downs and cursor summary | [Generic Streams data views](web/src/IntegrationStreams.tsx), contributed once to Integration forms by [the web addon](web/src/index.tsx) |

Event feeds compose their domain's idempotent ingest verb and never create links
or revisions. Messaging uses conversation partitions for Slack and mailbox
partitions for IMAP. Their legacy bridge cursor slices seed the first stream row
only. Subsequent progress belongs to that stream. `Bridge.cursor` remains for
Mount's existing cursor cleanup and Feed's declared backend contract, as well as
the first-generation messaging seeds; its presence does not authorize a second
cursor writer for an adopted stream.

Record replicas compare each side with its last applied base. An unchanged pair
does nothing; a remote-only change applies locally; a local-only change can be
conditionally written back by a push-capable adapter. Concurrent edits and
remote deletion versus a local edit remain open conflicts. A write-back returns
the authoritative remote version and content hash; retaining those facts with
the local projection identifies its later echo without relying on timestamps.
The adapter owns locking and validating the local projection before applying it.
Changes to mapping version or dependency digest also require application. The
adapter returns applied evidence in `ApplyResult`; only the driver promotes the
primary link. An adapter promotion of that link aborts the page. Optional
`prepare_page` locks a page's compound identities and targets once, before the
record savepoints; it and the visibility hooks perform database work only.

The driver extracts outside a transaction, then commits each page's database
effects, discrepancies and cursor together. Each record has a savepoint. A
declared semantic refusal quarantines that record; infrastructure failures roll
back the page and propagate. `finish_page` composes domain batch relationships
after successful rows are visible, before the cursor commits; messaging uses its
existing quotation owner here. External writes occur before their database
reflection and use the remote version precondition. If the process loses the
response, the next observation must reconcile that uncertainty; this protocol
does not promise an atomic commit across two systems.

Bounded callers resolve declarations through `open_stream`, use `begin_stream_cycle(stream, adapter)`
once, then `advance_stream` until its
result is exhausted, passing the returned stream after an epoch reset. The
caller closes its adapter. `push_stream` and `reconcile_stream` complete the
cycle when applicable. These functions contain no workflow runtime dependency;
execution composition belongs to `workflows_integrate`.

At cycle start, due non-conflict replica discrepancies with links are re-read
through the optional `read_keys(stream, keys, *, using)` adapter operation. It
returns one `RecordChange` for every requested external key, including a remote
tombstone when that key no longer exists. Transport runs outside transactions;
the shared page apply path commits the observations without changing the cursor,
phase or advancement timestamps. Successful apply resolves earlier non-conflict
failures for that identity. Event feeds are excluded from discrepancy rescan.
An adapter without `read_keys` requests a baseline instead, recording the fallback
and reason in the discrepancy details. No private work queue is retained.

Semantic quarantine increments `attempts` and sets an exponential retry delay
starting at one minute, capped by the smaller of a positive reconciliation
interval or 24 hours. A zero interval means continuous inventory reconciliation,
so it retains the 24-hour retry cap. Conflicts keep `retry_at=None` and require
explicit resolution before either side can be written again.
An invalid/expired cursor or `resync_required` creates a new baseline generation,
carrying existing links and quarantine forward while retaining revision history.

Inventory sweeps read and apply newly enumerated identities before incrementing
absence counts. Each `reconcile_stream(..., page_bound=100)` call commits one
bounded pulse; repeat while `_angee_reconcile` remains in the stream cursor.
Adapters implement `enumerate_keys(..., after=None, using=None)` as a stable
iterator with exclusive seek, and treat the reserved cursor member as opaque.
`read_keys` handles unseen identities as well as existing links. Without that
operation, the driver first extracts a separate bounded baseline. Root and child
absence passes also checkpoint their progress; completion alone updates
`last_reconciled_at`. First absence records unavailability, then a retained
tombstone at the declared threshold. `on_absent` runs once per status transition;
`on_revalidated` runs after each successful unchanged observation. Both share the
status transaction so domain visibility changes roll back with the link.

A compound link declares one immutable root `parent` in the same stream. Children
follow parent absence and retries read the parent's identity; their own successful
evidence and discrepancy resolution remain with the aggregate adapter. Sweeps
never delete domain rows or overwrite an open conflict. A peer beyond its
tombstone retention period must reverify a baseline. Link observation and
promotion preserve an omitted target; explicit `target=None` clears its binding.

Ownership declarations protect ordinary save, collection update, bulk and
delete paths. Source-owned fields change through `apply_external`, which checks
the locked row's immutable source identity and scope; locally owned fields stay
outside that command. Accounting import DTOs remain consumer-owned. Source-owned
many-to-many relations require an explicitly owned through model. These writes
currently fail closed on a non-default database because the upstream REBAC
permission checks do not accept an operation alias.
For a validated native lifecycle action, compose `run_external_transition` with
the explicit source identity and declared method name. It preserves transition
and save validation while verifying the complete changed field set before commit;
only source fields and native lifecycle bookkeeping may change. Custom transition
success hooks forward the explicit persistence callback. No ambient import
authority is installed; see the [ownership guideline](../../../docs/backend/guidelines.md#record-sync).
