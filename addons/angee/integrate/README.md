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
Adapter policy lives in `SyncStream.config`, seeded by `StreamDefinition` only
when the first epoch is created and preserved across epoch changes. A successful
conditional deletion returns `WriteBackResult(tombstone=True)` so the driver
retains the link's deletion and local origin. [Directory](../parties/README.md)
composes this protocol for bidirectional CardDAV contacts.

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

Inventory sweeps increment absence counts, first recording unavailability and
then a retained tombstone at the declared threshold. They never delete domain
rows or overwrite an open conflict. A peer that has not advanced within its
tombstone retention period must reverify a baseline.

Ownership declarations protect ordinary save, collection update, bulk and
delete paths. Source-owned fields change through `apply_external`, which checks
the locked row's immutable source identity and scope; locally owned fields stay
outside that command. Accounting import DTOs remain consumer-owned. Source-owned
many-to-many relations require an explicitly owned through model. These writes
currently fail closed on a non-default database because the upstream REBAC
permission checks do not accept an operation alias.
