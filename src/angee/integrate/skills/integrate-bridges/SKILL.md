---
name: integrate-bridges
description: "Adds a bridge or sync source on angee.integrate: a BridgeImpl adapter selected by ImplClassField, StreamDefinition partitions and SyncStream cursors, RecordLink replicas, credentials and OAuth presets, live sessions, tests. Use for 'add a bridge', 'sync a vendor', 'new channel backend'."
---

# Integrate Bridges

Run `angee-design` first. Integrate owns connection intent, credentials, scheduling and the stream driver; a
source addon contributes one adapter class, its registry key and its tests. Reference example throughout:
`src/angee/messaging_integrate_imap` (a `ChannelBackend` over IMAPClient); the generic test adapters live in
`tests/integrate_fixtures.py`.

## Use when

- "Sync `<vendor>` mail / contacts / repos / events into `<addon>`."
- "Add an `<x>_integrate_<vendor>` addon" or "a new channel / directory / VCS backend."
- "Add a live session (QR pairing, long-lived connection) for `<vendor>`."
- "Write back local changes to `<vendor>`" or "resolve a sync conflict."
- Not for the OAuth login flow itself (`iam_integrate_oidc`) or workflow-driven stream stages (`workflows_integrate`).

## Owners

| File or module | Owns | Reference |
|---|---|---|
| `src/angee/integrate/README.md` | The owner table: connection, epochs/cursors, links, failures, adapter contract, console | the owner table in `src/angee/integrate/README.md` |
| `impl.py` | `IntegrationImpl`, `BridgeImpl` (adapter contract), `LiveBridgeImpl` (session facts, `start_live`) | `src/angee/integrate/impl.py` |
| `models.py` | `Integration`, `Bridge` (scheduling, locks, live state), `SyncStream` + manager, `RecordLink` + manager, `Credential`, `OAuthClient` | `src/angee/integrate/models.py` |
| `states.py`, `streams.py` | `StreamDefinition` knobs; `StreamPage`, `RecordChange`, `ApplyResult`, errors, driver verbs | `src/angee/integrate/states.py`; `CursorInvalid`, `SemanticError`, `open_stream`, `advance_stream`, `reconcile_stream`, `sync_bridge` in `src/angee/integrate/streams.py` |
| `credentials.py`, `oauth/providers.py` | `CredentialKind` handlers; `OAuthProviderType` presets via `ANGEE_OAUTH_PROVIDER_TYPE_CLASSES` | `CredentialKind`, `CredentialKindHandler` in `src/angee/integrate/credentials.py`, `src/angee/integrate/oauth/providers.py` |
| `tasks.py`, `scheduler.py`, `constants.py` | Due-bridge and discovery dispatch, `run_bridge_session`, task names and tick constants; a manual request is `Bridge.request_sync` | `discover_connection`, `sync_bridge_now`, `sync_due_bridges`, `run_bridge_session`, `ensure_bridge_sessions` in `src/angee/integrate/tasks.py`; `enqueue_pending_discoveries`, `enqueue_due_bridges` in `src/angee/integrate/scheduler.py` |
| `session.py`, `session_runner.py`, `session_process.py`, `live.py` | Worker-only loop, outcome handling, fresh-interpreter host, console-safe pairing facts | `LiveSession` in `src/angee/integrate/session.py`, `run_bridge_session_job` in `src/angee/integrate/session_runner.py`, `BridgeSessionProcess` in `src/angee/integrate/session_process.py`, `PairingState` and `session_store_path` in `src/angee/integrate/live.py` |
| The capability's donor | The concrete bridge model a source extends (`messaging.Channel`, `parties.Directory`, `integrate_vcs.VcsBridge`) and its `ImplClassField` | `Channel` and its `backend_class` in `src/angee/messaging/models.py`, `ChannelBackend.registry_setting` in `src/angee/messaging/backends.py` |
| `testing/`, `tests/integrate_fixtures.py`, `tests/conftest.py` | Concrete `Integration`, `SyncStream`, `RecordLink`; `ConnectionAdapter`/`LiveConnectionAdapter`; `make_integration`, `current_stream` | `Integration`, `SyncStream`, `RecordLink` in `src/angee/integrate/testing/`; `ConnectionAdapter`, `LiveConnectionAdapter`, `ConnectionBridge` in `tests/integrate_fixtures.py`; `tests/conftest.py` |

## Reuse first

- The capability's backend base before `BridgeImpl` itself: `ChannelBackend` already implements `apply_record`
  through `Message.objects.ingest`, `finish_page`, `test_connection` and `deliver` (`src/angee/messaging/backends.py`).
- The stream driver: `open_stream`, `advance_stream`, `reconcile_stream`, `push_stream`, `sync_bridge`
  (`src/angee/integrate/streams.py`) own paging, cursor commit, quarantine, conflicts
  and parallelism; an adapter extracts and applies only.
- `Bridge.run_sync`, `claim_sync`, `record_sync_error`, `update_live_state`, `claim_live_account`
  (`src/angee/integrate/models.py`) own lifecycle, telemetry and locks.
- `Credential.auth_headers`, `ensure_fresh`, `Integration.fresh_credential` and `load_connection_credential`
  (`src/angee/integrate/models.py`) own token material and refresh; `HttpClientMixin`/`angee.integrate.http` own outbound HTTP with the SSRF pin.
- `IntegrationError(public_message, transient=, retry_after=)` (`src/angee/integrate/errors.py`) is the only
  exception an operator sees; `RequestNotSentError` marks safe retries.
- `LiveSession` (`src/angee/integrate/session.py`) owns the wake loop, QR reporting and cooperative stop;
  `session_store_path` (`src/angee/integrate/live.py`) owns the store convention.
- The stack rows for the protocol client ([Backend](../../../../../docs/stack.md#backend): the IMAPClient, neonize and telethon rows); parse with
  the library, never a hand-rolled protocol.

## Steps

1. **Create the addon and pick the donor.** Folder `src/angee/<capability>_integrate_<vendor>` (`angee-addon`);
   `[project].dependencies` names the capability/integrate distributions and vendor client
   (`src/angee/messaging_integrate_imap/pyproject.toml`). If the capability has no concrete bridge yet → add it
   there first as a `Bridge` child declared with `inherits="integrate.Integration"` and an `ImplClassField` selector
   (`Channel` in `src/angee/messaging/models.py`), never in the vendor addon. **Owner rule:** the capability owns
   the row, the vendor addon owns one implementation ([Repository Role](../../../../../AGENTS.md#repository-role)).

2. **Register the implementation key in `autoconfig.py`** (IMAP):

   ```python
   SETTINGS = {
       "ANGEE_CHANNEL_BACKEND_CLASSES.imap": "angee.messaging_integrate_imap.backend.ImapChannelBackend",
   }
   ```

   The dotted key merges into the capability's registry named by the base class `registry_setting`
   (`src/angee/messaging/backends.py`); `ImplClassField.check()` validates it. Seed the vendor row under
   `[resources] master` with `adopt = "slug"`. **Owner rule:** the registry setting and `ImplClassField` own
   selection ([stack](../../../../../docs/stack.md#backend) "Implementation-registry choice"); no addon-local lookup table.

3. **Write the adapter** (IMAP, abbreviated; `ImapChannelBackend` in `src/angee/messaging_integrate_imap/backend.py`):

   ```python
   class ImapChannelBackend(AnymailEmailChannelBackend):
       key = "imap"; label = "IMAP"; icon = "mail"
       defaults = {"vendor": "imap"}
       client_class = IMAPClient                       # seam tests replace with an in-memory server

       def streams(self, *, deadline=None) -> tuple[StreamDefinition, ...]:
           self._load_credentials(); client = self._connect()
           return tuple(StreamDefinition(key="messages", partition=name)
                        for name in sorted(self._select_mailboxes(client)))

       def extract(self, stream, page_bound, *, deadline=None) -> StreamPage:
           self._load_credentials()                     # row reads are fine here: no transaction is open
           ...  # bounded fetch; UIDVALIDITY change -> raise CursorInvalid()
           return StreamPage(records=messages, cursor=deepcopy(self._cursor), exhausted=not work.uids)

       def test_connection(self) -> str:
           ...  # dial, log in, log out; failures raise ImapError (an IntegrationError)
   ```

   `streams()` declares each partition once with its `StreamDefinition` knobs (`kind`, `direction`, `cursor`,
   `reconcile_interval`, `absence_threshold`, `tombstone_retention`, `snapshot`, `authoritative`, `mapping_version`, `config`;
   `StreamDefinition` in `src/angee/integrate/states.py`). `extract` returns one page and the cursor that covers it; the
   driver commits both. If the source is a mutable replica → return `RecordChange` rows and implement
   `enumerate_keys`, `read_keys` (set `supports_identity_reads = True`) and, for two-way, `local_changes` +
   `write_back` (`src/angee/integrate/impl.py`). If the capability base already applies →
   inherit `apply_record`; otherwise apply through the domain's idempotent ingest verb and return
   `ApplyResult(target=...)` (`ChannelBackend.apply_record` in `src/angee/messaging/backends.py`). **Owner rule:**
   [Integration and worker contracts](../../../../../docs/backend/guidelines.md#integration-and-worker-contracts): the cursor commits with the records it
   covers, the driver alone promotes links, adapters never persist cursors or credentials.

4. **Credentials and OAuth.** Read material through the row: `self.bridge.fresh_credential()`, then
   `credential.auth_headers()` or the handler's secret (`src/angee/integrate/models.py`); kinds are
   `CredentialKind` handlers (`src/angee/integrate/credentials.py`). If the vendor needs an OAuth preset →
   subclass `OAuthProviderType` with a unique `key` and contribute
   `"ANGEE_OAUTH_PROVIDER_TYPE_CLASSES.<key>"` (`src/angee/integrate/README.md`, `RefiningProvider` and `ContributedProvider` in `tests/integrate_fixtures.py`). If the
   connection must locate a remote resource first → `requires_connection_discovery = True` with
   `discover_connection`/`apply_discovery` (`IntegrationImpl` in `src/angee/integrate/impl.py`, `src/angee/integrate/README.md`).
   **Owner rule:** integrate persists credentials and the discovery fence; adapters return facts (`src/angee/integrate/README.md`).

5. **Live sessions (only for a long-lived connection).** Subclass `LiveBridgeImpl`: set `session_queue` (a
   dedicated queue; the stack runs it on the threads pool), `session_class` (a worker-only `LiveSession`
   subclass), and `session_isolation = "process"` when the vendor SDK can abort the interpreter
   (`LiveBridgeImpl` in `src/angee/integrate/impl.py`; fixture: `LiveConnectionAdapter` in `tests/integrate_fixtures.py`). The capability's row
   sets `live_impl_field` to its selector (`src/angee/messaging/models.py`). `start_live` enqueues
   `integrate.run_bridge_session` with `expires` of one tick (`LiveBridgeImpl.start_live` in `src/angee/integrate/impl.py`); the
   reconciler `ensure_bridge_sessions` restarts healthy connected bridges (`src/angee/integrate/tasks.py`).
   **Owner rule:** [Task pitfalls](../../../jobs/skills/jobs-tasks/references/pitfalls.md)
   "A long-lived Celery task needs the three-check wake loop": `LiveSession` owns it; a session never writes `lifecycle` ([Integration and worker contracts](../../../../../docs/backend/guidelines.md#integration-and-worker-contracts) "A lifecycle column is declared intent").

6. **Errors and operator messages.** Raise an `IntegrationError` subclass composed from facts the operator owns
   (`ImapError` in `src/angee/messaging_integrate_imap/backend.py`); pass `transient=`/`retry_after=` for throttling.
   Record-level refusals raise `SemanticError` (quarantined, page continues); infrastructure errors propagate
   (page rolls back) (`BridgeImpl.apply_record` in `src/angee/integrate/impl.py`). **Owner rule:** [Integration and worker contracts](../../../../../docs/backend/guidelines.md#integration-and-worker-contracts)
   "An integration failure reaches the operator only as an IntegrationError".

7. **Test the bridge.** Script the vendor, never patch the adapter: HTTP through `mock_transport`
   (`src/angee/integrate/testing/http.py`), a protocol client through the owner's in-memory server behind the
   `client_class` seam (`ImapServer` and `imap_server` in `src/angee/messaging_integrate_imap/testing/server.py`,
   whose scripted events cover withheld answers, expunges and refusals); the driver path with `make_integration(..., model=Channel,
   backend_class="imap")` and `current_stream(bridge, "messages", partition)` (`tests/conftest.py`), then
   `open_stream`/`advance_stream` or `Channel.run_sync` onto real tables (the `tests/test_messaging_imap.py` module docstring).
   Generic contract tests use `MemoryAdapter`/`ReadKeysAdapter` (`tests/test_integrate_streams.py`) and the
   `ConnectionBridge` fixture registered under `ANGEE_TEST_CONNECTION_CLASSES` (`tests/settings.py`). Live
   loops use `FakeLiveSession` (`tests/test_integrate_live.py`). Install `angee.integrate.testing` after
   `angee.integrate` in a bare host (`src/angee/integrate/testing/__init__.py`). **Owner rule:**
   [source-addon test models](../../../../../docs/checks.md#source-addon-test-models) own concrete adoption.

8. **Expose operator surfaces through the owners.** Connection test, pause/resume/disconnect, resync and
   conflict resolution already exist on the console schema and `Streams` views (the owner table in `src/angee/integrate/README.md`); a vendor
   addon adds only vendor-specific verbs on the donor row (`ImapChannelSampling` in `src/angee/messaging_integrate_imap/models.py`)
   through `angee-graphql` and `angee-pages`.

## Do not

- A private cursor table, checkpoint column or `last_synced_at` on the bridge: `SyncStream.cursor` and its generation own position ([Integration and worker contracts](../../../../../docs/backend/guidelines.md#integration-and-worker-contracts) "The cursor commits with the records it covers").
- Persisting the cursor, promoting a link or writing credential material inside an adapter: the driver and credential manager own those (`BridgeImpl.apply_record` in `src/angee/integrate/impl.py`, `src/angee/integrate/README.md`).
- Choosing a winner on divergent edits or a remote tombstone against a local edit: conflicts await explicit resolution ([Integration and worker contracts](../../../../../docs/backend/guidelines.md#integration-and-worker-contracts) "Compare both mapped sides with their pinned applied base").
- Running `extract`/`enumerate_keys` inside an enclosing transaction, or network IO inside `prepare_page`/`apply_record`: extraction runs outside the page transaction and may read the row (the IMAP adapter reloads credentials per page through `fresh_credential`, `ImapChannelBackend._load_credentials` in `src/angee/messaging_integrate_imap/backend.py`, `src/angee/integrate/models.py`); the driver refuses extraction in an atomic block (`advance_stream` in `src/angee/integrate/streams.py`) and application does database work only (`src/angee/integrate/impl.py`; [Integration and worker contracts](../../../../../docs/backend/guidelines.md#integration-and-worker-contracts) "The cursor commits with the records it covers").
- Re-raising a vendor exception's text or passing `str(exc)` to the operator ([Integration and worker contracts](../../../../../docs/backend/guidelines.md#integration-and-worker-contracts) "An integration failure reaches the operator only as an IntegrationError").
- A `LiveBridgeImpl` without `session_queue`, which would put a session that outlives a tick on the shared prefork queue: `start_live` refuses it (`LiveBridgeImpl.start_live` in `src/angee/integrate/impl.py`; [Task pitfalls](../../../jobs/skills/jobs-tasks/references/pitfalls.md) "A long-lived Celery task needs the three-check wake loop").
- A worker writing `Integration.lifecycle` to report an outcome: report on `runtime_status` and the live desire ([Integration and worker contracts](../../../../../docs/backend/guidelines.md#integration-and-worker-contracts) "A lifecycle column is declared intent").
- A second scheduler, polling loop or `models_with` registry for bridges: `integrate.sync_due_bridges` and `ensure_bridge_sessions` own dispatch (`src/angee/integrate/tasks.py`; [Integrate pitfalls](references/pitfalls.md) "Bridge scheduling follows the final MTI kind").

## Verify

Done means: the key resolves through the capability's `ImplClassField` (its `check()` passes), `streams()`
declares each partition once, a driver round trip commits records with their cursor and a repeated run is
idempotent, `test_connection` returns an operator message and raises only `IntegrationError`, and (live) the
session exits within one wake on stop, shutdown and lost lock.

- In a lane (jj workspace: no host, database or JS install): focused pytest on the addon's tests plus
  `tests/test_integrate_streams.py`, ruff and mypy on touched files, `tests/test_layering.py` —
  [Python and composition contracts](../../../../../docs/checks.md#python-and-composition-contracts). PostgreSQL-only
  concurrency modules skip on SQLite and are reported separately.
- Only on integrated `src`: composed host boot, resources load of the vendor row, SDL/codegen for any schema
  change, the stack's queue worker for the session queue, and a browser pass over the Streams tab —
  [composition and schema](../../../../../docs/checks.md#composition-and-schema), [browser verification](../../../../../docs/checks.md#browser-verification).
- Loop: `.agents/skills/slice/SKILL.md`. False green: in-memory client tests prove parsing and paging, not the
  cursor commit; only a driver test on real tables does ([what CI actually runs](../../../../../docs/checks.md#what-ci-actually-runs)).

## Pitfalls

- Declaration knobs `cursor`, `reconcile_interval`, `absence_threshold`, `tombstone_retention`, `config` seed the first generation only; a changed `direction`/`snapshot` or increased `mapping_version` opens a new baseline and a changed `kind` is refused (`src/angee/integrate/README.md`).
- A declared snapshot without `reconcile_interval` closes after its baseline; a missing interval alone never means read-once ([Integration and worker contracts](../../../../../docs/backend/guidelines.md#integration-and-worker-contracts) "Read-once requires a snapshot source and closure").
- Advisory locks are process-local on SQLite: `claim_account` is serialized by the lock, not the database (`claim_account` in `src/angee/integrate/impl.py`, `task_locks_are_cross_process` in `src/angee/base/locks.py`).
- A child capability's second intent axis (live desire) is unset by generic lifecycle verbs; the reconciler selects on lifecycle and closes it ([Integrate pitfalls](references/pitfalls.md) "An Integration child's second intent axis").
- A latching `runtime_status=ERROR` gate must be cleared by the repair verb itself ([Integrate pitfalls](references/pitfalls.md) "A latching gate needs a reset").

## Read next

- [Integrate pitfalls](references/pitfalls.md)
- [Integration and worker contracts](../../../../../docs/backend/guidelines.md#integration-and-worker-contracts)
- [Integrate README](../../README.md), [Stack: backend](../../../../../docs/stack.md#backend): the authlib, httpx2 and provider SDK rows
- Skills: `angee-addon`, `angee-models` (the donor row), `jobs-tasks` (queues, budgets), `angee-resources` (the vendor row), `angee-tests`, `slice`

## Sources

<!--
Docs: src/angee/integrate/README.md (whole); docs/backend/guidelines.md sections Record sync, Integrations and workers, Integration and worker contracts (cited by anchor and bullet title); docs/stack.md #backend (messaging-protocol rows and the implementation-registry choice); docs/checks.md #python-and-composition-contracts, #source-addon-test-models, #composition-and-schema, #frontend-packages-and-addon-fragments, #browser-verification, #what-ci-actually-runs; AGENTS.md (Repository Role).
Code: src/angee/integrate/{impl.py,states.py,streams.py (def list),models.py (def list),session_process.py,live.py,session.py,session_runner.py,sync_runner.py,tasks.py,scheduler.py,queue.py,constants.py,autoconfig.py,pyproject.toml,credentials.py (def list),errors.py (def list),discovery.py (def list),locks.py (def list),testing/*};
src/angee/messaging/backends.py; src/angee/messaging/models.py; src/angee/messaging_integrate_imap/{pyproject.toml,autoconfig.py,README.md,backend.py,models.py};
src/angee/platform_integrate_vcs/pyproject.toml; tests/{integrate_fixtures.py,conftest.py,settings.py,stream_adapters.py,test_integrate_streams.py,test_messaging_imap.py,test_integrate_live.py}.
Doc gaps: no docs/ walkthrough for writing an adapter; the contract is in impl.py docstrings and the README; derived from IMAP and the test adapters.
Mismatches: platform_integrate_vcs is a catalog tier, not a bridge (its bridge is integrate_vcs.VcsBridge); src/angee/integrate/README.md links owner modules by bare filename while docs/ link with paths.
-->
