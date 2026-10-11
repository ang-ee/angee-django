# Integrations and record sync

An integration owns connection intent, credentials and permissions. A concrete
bridge supplies a backend that declares independently ordered stream partitions.
The shared driver handles bounded pages, conditional writes and reconciliation;
domain adapters own protocol parsing and ingest policy.

| Concern | Owner |
|---|---|
| Connection, scheduling, queue admission and run telemetry | [Integration and Bridge](models.py) |
| Epochs, cursors, snapshot closure and reconciliation policy | [SyncStream and its manager](models.py) |
| External identity, observations, applied base, outbox and pin policy | [RecordLink and its manager/queryset](models.py) |
| Inbound failure slots and retry lifecycle | [SyncStateMixin](mixins.py) and [SyncFailure](states.py) |
| Adapter contract and bounded execution | [BridgeImpl](impl.py) and [stream driver](streams.py) |
| Operator actions and inspection | [Console schema](schema.py), [Streams views](web/src/IntegrationStreams.tsx) and [Integration permissions](permissions.zed) |

Event feeds are append-only and compose their domain's idempotent ingest verb.
They create no replica links. Their unlinked failures belong to the stream's
`inbound` state; closed slots retain evidence without a stream history table.
Stream cursors are plain finite JSON and commit with the records they cover.
Transport runs outside the page transaction; record refusals use savepoints so
later records can continue, while infrastructure failures roll back the page.

Record replicas compare JSON-native local and remote mapped projections against
their protected `applied_base`, a snapshot in the link's own native history.
Observation never advances that base. Remote-only changes apply locally;
local-only changes may write back with a remote version fence. Divergent edits
and remote deletion against a local edit require an explicit choice. Identical
edits converge, as accepted by the commit 8 review ruling. Adapters revalidate
their local projection under domain locks before applying.

A conditional write returns authoritative source evidence and version, retained
with the local projection so the driver recognizes its next pull echo. Separate
JSON Patch `test` operations check each retained reflection precondition beside
the transport's remote-version fence. Neither makes the two systems atomic.
Only the driver promotes a primary link; adapters return `ApplyResult` evidence.

Each applied record retains the observe row and the application-evidence row
(commit 8 review ruling). Pinning `applied_base` creates no pointer-only history
row. History retention is unbounded: deployments budget both full snapshots and
the snapshots from generation changes, when retained links follow the new epoch.
The Streams history list shows hashes, versions and application facts; payloads
load on demand through their authorized link.

A baseline adopts matching local identities without an applied base using remote
values. Local-only rows may then be created remotely when a writable baseline
completes. Before enabling two-way sync, review the synchronized local scope.
Direction or snapshot changes open a new baseline generation and retain links,
policy and failures. Adapter-private deletion policy remains in stream `config`.
A declared snapshot with no reconciliation interval closes after its baseline;
explicit resync opens a new generation. Missing cadence alone never means read-once.

Several inbound failures can coexist. Semantic failures use bounded backoff;
conflicts await an explicit remote/local choice. Due replica failures re-read
identity through `BridgeImpl.read_keys`; unsupported identity reads fall back to
a baseline with the reason retained in failure details. Refused writes retry
through the outbox. A due refusal with no remaining local candidate closes.
`RecordLink.pin()` withholds automatic application and push; `unpin()` resumes
them. Admin actions dispatch to those owners.

Inventory and absence reconciliation are resumable bounded pulses. Compound
children follow their immutable aggregate parent for absence and retries; the
adapter owns child application evidence. Neither reconciliation nor generation
changes silently discard open failures or domain rows.

See the [Record sync guideline](../../../docs/backend/guidelines.md#record-sync)
for invariants, the owners above for exact contracts, and
[`preview_push`](streams.py) for read-only push inspection. The
[Directory addon](../parties/README.md) composes this protocol for CardDAV.

RecordLink declares `merge_policy = MOVE`: merging a target moves its links to
the survivor. Both remote cards receive the survivor's projection, subject to
each stream's write-back policy and remote preconditions.
