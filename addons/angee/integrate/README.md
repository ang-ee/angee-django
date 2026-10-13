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
The Streams tab's link history drill-down is the provenance
[History timeline](../provenance/README.md#history-surface) for the link: one
entry per sync changeset with its hash, version, application facts and payloads
as field deltas, read through the link's own authorization.

A baseline adopts matching local identities without an applied base using remote
values. Local-only rows may then be created remotely when a writable baseline
completes. Before enabling two-way sync, review the synchronized local scope.
A declaration's `authoritative` flag marks the source as the system of record for
what the stream states (`SyncStream.authoritative`, read by statement survivorship);
changing it re-derives the linked subjects through `SyncStreamManager.set_authoritative`.
Direction or snapshot changes open a new baseline generation and retain links,
policy and failures. Adapter-private deletion policy remains in stream `config`.
A changed declaration reaches an existing stream when a drain opens it:
[`open_stream`](streams.py) always reconciles the stream with its declaration, on
the generic sync and on a workflow cycle alike. What reaches it is limited to the
declaration's facts: `authoritative` flips and re-derives, a changed `direction` or
`snapshot` opens a new baseline generation (a full re-read of that stream), and a
changed `kind` is refused. `cursor`, `reconcile_interval`, `absence_threshold`,
`tombstone_retention` and `config` seed the first generation only; changing them in
a declaration never changes an existing stream.
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
Provider presets are contributed through the existing implementation registry:
subclass `OAuthProviderType`, declare a unique `key`, and contribute
`"ANGEE_OAUTH_PROVIDER_TYPE_CLASSES.<key>"` from the owning addon's autoconfig.
`OAuthClient.provider_type` choices and defaults come from `ImplClassField`.
The provider's `refine_grant(protocol, tokens)` runs after code exchange and
before profile lookup or credential persistence. It can call
`OAuthClientProtocol.exchange_grant(grant_type, **grant)` for an additional
wire exchange. Those names distinguish refinement from a token request.
Product presets and registrations belong to their capability addons.

Connection adapters declare `IntegrationImpl.requires_connection_discovery = True`
to opt into asynchronous `discover_connection`/`apply_discovery`. Manual and
credential-only implementations leave it false. Overriding a method alone does not enable discovery.

`Bridge.dispatch_inbound` verifies and lands through the concrete capability
under its polling advisory lock. Call it outside atomic blocks so the lock
covers committed landing. A busy bridge raises transient `IntegrationError`;
the inbound transport must arrange redelivery.

Provider presets can compute `userinfo_params(protocol, access_token)`;
connect and OIDC login/link pass them to
`OAuthClientProtocol.fetch_userinfo(access_token, params=…)`.
Provider proofs and query vocabulary stay in the provider addon. Connect and
OIDC login/link share `OAuthClientProtocol.exchange_identity`, which exchanges,
refines and reads userinfo. OIDC verifies the nonce-bound ID token from the code
exchange, independently of the refined material stored for API access.
Server-side signing reads confidential material only
through `OAuthClientProtocol.confidential_client_secret()`; public clients are
refused before secret storage is accessed; confidential clients without a secret
are refused too. `OAuthClient.is_public_client` owns that classification.

`OAuthClient.configuration_state` is the readiness rule used by the picker,
connect and the operation-local [registration snapshot](oauth/registrations.py).
The snapshot reads public configuration fields, never client secrets. OIDC
metadata discovery resolves OAuth endpoints independently of connection
discovery. Host
settings are applied through `oauth_clients` after resources load.
`Credential.is_reconnect_required` uses status, failed refresh and expiry.
Refreshability is derived by its kind handler from the current client and grant
material; it is never stored as a copy of client policy. Ordinary access-token
expiry with a refresh grant does not ask for new consent.

Connection discovery locates the external resource; a REBAC binding grants a
relationship. Integration owns the `discovery_*` handshake, and the task is
named `integrate.discover_connection`. Every credential attach
enters `await_discovery`, clears completion and advances `discovery_generation`.
No-op discovery settles synchronously. The worker runs
`IntegrationImpl.discover_connection(credential) -> ConnectionDiscovery` outside
transactions under the sync advisory lock. The result contains adapter-owned
`data` and, optionally, a `DerivedCredential(kind, name, material)`.

`finish_discovery(credential_pk=…, generation=…, discovery=…)` locks the
integration, verifies both original task values, and invokes the adapter's
`apply_discovery(discovery)` with a freshly loaded row. This hook performs only
database work. Integrate persists derived material through the credential
manager and `attach_connection`; adapters never persist credentials or implement
the generation fence. Integrate then settles and sends
`signals.discovery_finished.send(sender=type(instance), instance=instance)` on commit.
The source grant is retained in `discovery_credential` so reconnect can regenerate
resource-specific credentials. Ordinary refresh preserves discovery. Re-consent
preserves direct attachments but regenerates tokens derived from the old grant.
A callback also reconnects a disconnected attachment of the same credential.
A changed credential identity
invalidates its attachments, preserving pauses. Disconnecting a source grant
releases its derived attachments and disposes of unused derived material. Switching
the external account on an in-use credential is refused.

The minute scheduler redispatches due pending discovery, with robust sends and
independent scheduler steps. Transport errors, timeouts and server failures
retry with exponential backoff capped at one hour; a provider horizon wins when
it is longer. Five remote attempts exhaust one generation. Lock contention consumes no
attempt. Retry discovery admits the integration owner and platform admins;
Resume, Pause and Disconnect remain platform-admin operations. The permissions
are declared by integrate's Zed schema and projected in the row query.
A repeated lifecycle value is a no-op. Required connection discovery
suspends live desire through `Bridge.update_live_state(desired=STOPPED, …)` so
an existing session cooperatively releases the advisory lock. It leaves sync
stages and retained workflow runs intact. `discovery_ready` / `ready_for_work()`
are the shared admission rule; generation zero preserves existing integrations
until their next attachment or Resume. There is no separate polling repair scan.

`IntegrationError(public_message, transient=…, retry_after=…)` owns the optional
positive provider horizon. `RequestNotSentError` declares that connect, DNS or
TLS handshake failed before HTTP request bytes were written and is safe to retry.
Failures after sending retain an uncertain transport outcome. A hint implies transience unless explicitly declared
otherwise. Aggregate failures retain a partition's horizon even when another
partition has a permanent refusal. `BridgeSyncError` retains stream
and partition identity and is transient when all failed partitions are transient,
using their longest hint. Bridge schedules at the later of its normal interval
and that hint, while paused rows stay unscheduled.

Concrete types compose `IntegrationOAuthMixin` (included in `BridgeTypeMixin`)
and the public connection projection. Native scalar annotations read credential
status, vendor hints and the selected concrete implementation through elevated
subqueries. Native prefetch hints batch credential-owned refresh evidence, and
REBAC permission projections batch the declared admissions. Credential status
and reconnect health are null for readers who cannot write the integration; secret material is never
projected. Live sessions require completed discovery in addition to healthy
runtime status.
`ConnectOAuthButton` accepts optional vendor-owned labels. Its optional `row`
path consumes `CONNECT_RECORD_FIELDS`; existing callers may keep their own
selection and supply `start({redirectUri, next})` without a row.

Generic Hasura resources with an editable credential use
`IntegrationWriteBackend`; authored patches use `apply_integration_patch_fields`.
Generic patches resolve credential references through the caller's native write
queryset, so hidden credentials are refused as not found. Authored console
patches retain their administrator gate. Both dispatch attachment to the model
owner.

