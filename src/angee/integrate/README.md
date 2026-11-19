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

Manual requests and scheduled polls share `Bridge.mark_sync_queued` admission.
A live sync lock or retained workflow run keeps its progress and declines a new
task; a declined scheduled poll still claims its interval
(`Bridge.admit_due_sync`). A stale syncing marker without either owner remains
recoverable. Callers use `Bridge.request_sync`'s result to report whether they
queued work.
`Integration.request_sync` requests every bridge capability of one integration
and counts the syncs it queued.

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
edits converge. Adapters revalidate their local projection under domain locks
before applying.

A conditional write returns authoritative source evidence and version, retained
with the local projection so the driver recognizes its next pull echo. The link
checks its complete retained reflection precondition beside the transport's
remote-version fence. Neither makes the two systems atomic.
Only the driver promotes a primary link; adapters return `ApplyResult` evidence.
The stream declaration alone owns `mapping_version`. The driver stamps it at
promotion, write-back reflection and quarantine; `RecordChange`, `LocalChange`
and `ApplyResult` carry no mapping-version copies. Adapters return only the evidence
apply learned.

A push adapter may version its local projection. It yields each row's exact
`LocalChange.local_version`, read with the projection, and the push records the
version it read beside the base it wrote (`RecordLink.local_version`). A full
read may then skip every row a link already holds at its current version
(`RecordLinkManager.changed_since_base`). A projection that reads only the row's
own tracked columns, plus a container setting, takes the row's history version
(`RecordLinkManager.with_local_versions`, the setting folded into a digest so a
version stays within `LOCAL_VERSION_LENGTH`). Versions are compared for equality,
never by time: a timestamp is stamped before commit, so a base could postdate an
uncommitted edit. A push also records the newer version of a row touched without
a change to its projection, and baselines a row read without one (its history
predates capture or was pruned) so the next push settles it
(`RecordLinkManager.settle`). A mapping change reads every row again: raise the
stream's `mapping_version` with any change to the projection. What no version
names (such a change left unraised, or a write that skipped history capture)
converges at the stream's sweep: while `SyncStream.sweep_due`, once per
`reconcile_interval`, the read skips nothing, and a push stream's push completes
that sweep.

Each applied record retains the observe row and the application-evidence row;
a record delivered again unchanged retains only its observe row. Pinning `applied_base` creates no pointer-only history row. History retention is unbounded: deployments budget both full snapshots and
the snapshots from generation changes, when retained links follow the new epoch.
The Streams tab's link history drill-down is the provenance
[History timeline](../provenance/README.md#history-surface) for the link: one
entry per sync changeset with its hash, version, application facts and payloads
as field deltas, read through the link's own authorization.

A baseline adopts matching local identities without an applied base using remote
values. Local-only rows may then be created remotely when a writable baseline
completes. Before enabling two-way sync, review the synchronized local scope.
The adapter's `StreamDefinition` declaration owns every stream policy knob.
Its `authoritative` flag marks the source as the system of record for
what the stream states (`SyncStream.authoritative`, read by statement survivorship);
changing it re-derives the linked subjects through `SyncStreamManager.set_authoritative`.
Direction, snapshot, newer mapping-version and changed mapping-contribution
declarations open a new baseline generation and retain links, policy and failures.
Adapter-private deletion policy remains in stream `config`.
A changed declaration reaches an existing stream when a drain opens it:
[`open_stream`](streams.py) always reconciles the stream with its declaration, on
the generic sync and on a workflow cycle alike. What reaches it is limited to the
declaration's facts: `authoritative` flips and re-derives, a changed `direction` or
`snapshot`, an increased `mapping_version` or a changed `mapping_contributions` list
opens a new baseline generation (a full re-read of that stream), and a changed
`kind` is refused. The generation records
its active mapping version, so reopening the same declaration preserves progress.
A lower declaration preserves the cursor and generation but updates that active
version: rollback output must retain its actual mapping. Returning to the newer
declaration starts a baseline to revisit any output the rollback wrote. Adapters
declare the version once in `StreamDefinition`; the driver stamps all evidence. An unknown
stored version is seeded from the minimum applied link version, or the declared
version for a stream without applied links; never-applied observations and
quarantines do not lower it. Only a newer declaration starts a baseline.
`cursor`, `reconcile_interval`, `absence_threshold`,
`tombstone_retention` and `config` seed the first generation only; changing them in
a declaration never changes an existing stream.

A mapping composed from several declarers (a capability's base and the consumer
addons that map more source fields) declares `mapping_contributions` instead: one
`MappingContribution(key, version, sequence=...)` per part. Integrate applies the
parts in sequence order, ties broken by key, whatever order they were collected
in. The stream then owns the version. Its generation retains the ordered keys and
versions it ran (`SyncStream.mapping_contributions`), and any other list (a part
added, removed, exchanged, reordered or re-versioned) opens one baseline under a
version above the retained one, above every version its records were applied
with and at least the parts' sum, so every record replays once and a version
never stamps two lists' output. A renumbering that keeps the order changes
nothing. The retained list keeps its version, and a composed stream's version
never lowers: a plain version declared on it later reads as one part under the
empty key, which no declared part may use. A declaration with contributions
leaves `mapping_version` at its default; any other version beside them is
refused. The list is a deployment-wide fact: workers declaring different lists
for one stream open a new baseline on every open. Streams written before the
column hold the empty list and keep the plain rule.

A declared snapshot with no reconciliation interval closes after its baseline;
explicit resync opens a new generation. Missing cadence alone never means read-once.

A [container](../../../docs/glossary.md#backend) (an address book, a
calendar) is a record of a parent stream, declared with
[`holds_containers`](states.py), and the partition of a child stream whose
declaration names the container's link (`StreamDefinition.container`). The
container row is created on the parent's apply path like any record. A container is live, and its child
declared, only while
[`RecordLinkQuerySet.live_containers`](models.py) admits it: a container whose
absence is being counted pauses its child, and one that stops observing retires
it ([`SyncStreamManager.retire_children`](models.py)); the retirement pass
tombstones the child's links and deletes nothing. The
[driver](streams.py) (`sync_bridge`) owns ordering, deadlines and re-declaration.

A replica identity longer than [`EXTERNAL_KEY_LENGTH`](states.py) can hold no link: the
driver records one coalesced stream failure for it, and the rest of the page applies.
Several inbound problems can coexist. For one record link, new source or mapping
evidence closes older versions of the same non-conflict problem while retaining
their history. Stream-level event failures remain independent.
Semantic failures use bounded backoff;
conflicts await an explicit remote/local choice (`RecordLink.resolve_conflict`). Due replica failures re-read
identity through `BridgeImpl.read_keys`; unsupported identity reads fall back to
a baseline with the reason retained in failure details. Refused writes retry
through the outbox. A due refusal with no remaining local candidate closes.
A push stream never applies its remote: keeping the remote of its conflict
records the remote's current version as the link's base over the local
projection, and the next local change writes over it. A push link holds no row
(`RecordLink.live_reference_filter`): deleting the row releases the link
(`RecordLinkManager.release_pushes`), and the push writes its removal as
`write_back(link, None)`.
`RecordLink.pin()` withholds automatic application and push; `unpin()` resumes
them. Admin actions dispatch to those owners.

Inventory and absence reconciliation are resumable bounded pulses. Compound
children follow their immutable aggregate parent for absence and retries; the
adapter owns child application evidence. Neither reconciliation nor generation
changes silently discard open failures or domain rows.

See the [Integration and worker contracts](../../../docs/backend/guidelines.md#integration-and-worker-contracts)
for invariants, the owners above for exact contracts, and
[`preview_push`](streams.py) for read-only push inspection. The
[Directory addon](../parties/README.md) composes this protocol for its directory
backends, CardDAV among them, each syncing in the direction its backend declares.

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
`OAuthClient.token_endpoint_auth_method` selects body or HTTP Basic client
authentication for both token-request formats. `OAuthClient.token_identity_keys`
names the non-secret token-response keys connect reads the account from when
userinfo cannot; token material is never among them. Presets seed both through
`defaults`, like the other registration fields.
Product presets and registrations belong to their capability addons; a vendor
whose one client serves several capabilities has its own addon
([`integrate_google`](../integrate_google/__init__.py)).

One client serves every consumer of a provider: a capability declares its client
and scopes once (`OAuthConnectIntent` in [`impl.py`](impl.py)). The owners of the
connect rules are `OAuthClient.requested_scopes` (scopes),
`CredentialManager.upsert_for_user` (one credential per external account and the
grant checks), `Integration.attach_connection` (the account binding),
`Integration.credential_blocker` (which credentials a capability accepts: the
refusal `admit_credential` raises on every attachment and the connect picker
shows), `Integration.reads_with` (an OAuth grant of the capability's
client covering its scopes), `Integration.connectable_credentials` (the user's
accounts it reads with, offered from an unsaved row before the capability
exists), `OAuthProviderType.authorize_params` (the prompt),
[`connect.complete_account_connect`](connect.py) (account identity: userinfo, the
`token_identity_keys`, then the verified ID token) and
`connect.complete_external_account_link` (the consent restart). A credential stored
without an account, before credentials were per account, and the integrations it
serves without a binding bind to the first account that connects.

A capability connected with one of the user's accounts projects its connect facts
through the `ConnectWithAccount` GraphQL interface ([`schema.py`](schema.py)),
built with `ConnectWithAccount.of` from an unsaved row (`IntegrationManager.build`).
Its query spreads the `ConnectWithAccountFields` fragment and is read through
`useConnectWithAccountQuery`, which reloads it as the user's accounts change; its
dialog composes `ConnectWithAccountAction` (given the capability's authored connect
mutation over `{ credentialId }`, which its `MutationDialog` runs), or
`useConnectWithAccountField` among its other fields
([`ConnectWithAccountAction.tsx`](web/src/connect/ConnectWithAccountAction.tsx)):
the account choice and "Connect another account" with the capability's scopes.
An account the capability refuses (its `blocked_reason`, the capability's
`credential_blocker`) is listed with that reason and cannot be chosen.
While the capability has no ready OAuth client (`oauth_client` is null) and the
user holds no account it reads with, the field says the connection is not
configured instead, and links an administrator to the OAuth providers page.

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
refines and reads userinfo. OIDC login verifies the nonce-bound ID token from the
code exchange, independently of the refined material stored for API access.
Server-side signing reads confidential material only
through `OAuthClientProtocol.confidential_client_secret()`; public clients are
refused before secret storage is accessed; confidential clients without a secret
are refused too. `OAuthClient.is_public_client` owns that classification.

`OAuthClient.configuration_state` is the readiness rule;
`OAuthClientQuerySet.ready_registrations` applies it to each slug's preferred
registration for the picker, connect and the operation-local
[registration snapshot](oauth/registrations.py), which the console projections
pass to `Integration.is_oauth_connectable`. Readiness reads public
configuration fields, never client secrets. OIDC
metadata discovery resolves OAuth endpoints independently of connection
discovery. Host
settings are applied through `oauth_clients` after resources load.
`Credential.is_reconnect_required` uses status, failed refresh or mint, and expiry.
A `jwt_bearer` credential holds a provider signing key (a pasted key file, such as a
Google service-account key) and mints one access token per scope set through the
JWT bearer grant (RFC 7523); consumers call `Credential.auth_headers(scopes=...)`.
Only a refusal of the key or identity marks it failed, re-entering the key file
clears that, and the signing key is never its generic secret.
`Credential.refreshable` derives refreshability from the current client and grant
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

A vendor's JSON request is `HttpClient.request_model` ([outbound HTTP](http.py)):
one pinned, size-capped request whose answer is validated into a Pydantic model;
a capped request asks for an unencoded answer. Whatever brings it no usable
answer is one `HttpRefusal`, classified once: a transport failure, a 429 or a
5xx is transient and waits for the answer's `Retry-After`; any other status, and
a success that is over the cap, cannot be decoded or does not validate, are
permanent, because the same request brings the same answer. The status decides
before the body does: a refused answer whose body is over the cap or cannot be
decoded keeps its status, with no body, and is never read past the cap. A
whole-resource poll reads a permanent answer again on its next run, a pager
shrinks its page, and one record's oversized or unreadable answer is that
record's refusal, quarantined while the page of results continues. The vendor's
`error=` factory turns the refusal into its `IntegrationError` with
`HttpRefusal.as_error(error, service, *, public_message=None, retry_after=None)`,
whose default message names the service and nothing the answer said. The vendor
states its own cases through that hand-off: its words as `public_message`, and
a wait its error body names as `retry_after`, used instead of the answer's
`Retry-After`. It builds the error of a status it adds as transient by hand. A
refusal keeps the failure that stopped its request: its `cause` tells a request
`not_sent` from one `unreachable` after it may have been sent.

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
selection and supply `start({redirectUri, next})` without a row. A vendor addon
whose saved record connects through an OAuth registration contributes
`connectIntegrationAction()` at its own model's `#actions`.

Integration capabilities use native Hasura resources with
`IntegrationWriteBackend`. Its creation factory prepares implementation defaults
before validation; updates dispatch connection, lifecycle, ownership and config
changes through `Integration.prepare_patch`. Credential references resolve through
the caller's native write queryset, so hidden credentials are refused as not
found. Provider and VCS console writes retain their administrator gate while
their forms inherit the generated resource's writable and required fields.

`Integration`, `WebhookSubscription` and `ExternalAccount` compose `OwnerMixin`:
`owner` is a column, never a stored tuple. An editable integration owner changes
through `transfer_ownership`, including native Hasura patches. A
platform-managed integration is inserted with `save(ownerless=True)`. An external
account takes only the owner its linking verb names; that verb claims an unowned
account, and disconnect releases only the caller's ownership. Its transfer is
admin-only because sign-in resolves an identity to its owner. A credential's
`user` relation names whose authority it carries; it is not ownership.
