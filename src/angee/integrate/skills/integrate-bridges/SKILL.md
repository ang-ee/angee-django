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
| `src/angee/integrate/README.md` | The owner table: connection, epochs/cursors, links, failures, adapter contract, console | `src/angee/integrate/README.md:8-15` |
| `impl.py` | `IntegrationImpl`, `BridgeImpl` (adapter contract), `LiveBridgeImpl` (session facts, `start_live`) | `src/angee/integrate/impl.py:34,65,183,206` |
| `models.py` | `Integration`, `Bridge` (scheduling, locks, live state), `SyncStream` + manager, `RecordLink` + manager, `Credential`, `OAuthClient` | `src/angee/integrate/models.py:292,1134,1697,2754,3748,3956,4134,4389` |
| `states.py`, `streams.py` | `StreamDefinition` knobs; `StreamPage`, `RecordChange`, `ApplyResult`, errors, driver verbs | `src/angee/integrate/states.py:101`, `src/angee/integrate/streams.py:135,146,181,211,219,429,914,1114,1229` |
| `credentials.py`, `oauth/providers.py` | `CredentialKind` handlers; `OAuthProviderType` presets via `ANGEE_OAUTH_PROVIDER_TYPE_CLASSES` | `src/angee/integrate/credentials.py:17,33`, `src/angee/integrate/oauth/providers.py:14` |
| `tasks.py`, `scheduler.py`, `queue.py`, `constants.py` | Due-bridge and discovery dispatch, `run_bridge_session`, task names and tick constants | `src/angee/integrate/tasks.py:31,48,60,77,84`, `src/angee/integrate/scheduler.py:21,39` |
| `session.py`, `session_runner.py`, `session_process.py`, `live.py` | Worker-only loop, outcome handling, fresh-interpreter host, console-safe pairing facts | `src/angee/integrate/session.py:60`, `src/angee/integrate/session_runner.py:27`, `src/angee/integrate/session_process.py:37`, `src/angee/integrate/live.py:60,100` |
| The capability's donor | The concrete bridge model a source extends (`messaging.Channel`, `parties.Directory`, `integrate_vcs.VcsBridge`) and its `ImplClassField` | `src/angee/messaging/models.py:1032,1049,1051`, `src/angee/messaging/backends.py:224,232` |
| `testing/`, `tests/integrate_fixtures.py`, `tests/conftest.py` | Concrete `Integration`, `SyncStream`, `RecordLink`; `ConnectionAdapter`/`LiveConnectionAdapter`; `make_integration`, `current_stream` | `src/angee/integrate/testing/{integration.py:7,models.py:13,23}`, `tests/integrate_fixtures.py:25,75,81`, `tests/conftest.py:336,380` |

## Reuse first

- The capability's backend base before `BridgeImpl` itself: `ChannelBackend` already implements `apply_record`
  through `Message.objects.ingest`, `finish_page`, `test_connection` and `deliver` (`src/angee/messaging/backends.py:224-330`).
- The stream driver: `open_stream`, `advance_stream`, `reconcile_stream`, `push_stream`, `sync_bridge`
  (`src/angee/integrate/streams.py:1114,429,914,689,1229`) own paging, cursor commit, quarantine, conflicts
  and parallelism; an adapter extracts and applies only.
- `Bridge.run_sync`, `claim_sync`, `record_sync_error`, `update_live_state`, `claim_live_account`
  (`src/angee/integrate/models.py:3414,3174,3337,2926,2903`) own lifecycle, telemetry and locks.
- `Credential.auth_headers`, `ensure_fresh`, `Integration.fresh_credential` (`src/angee/integrate/models.py:1297,1341,2068`)
  own token material and refresh; `HttpClientMixin`/`angee.integrate.http` own outbound HTTP with the SSRF pin.
- `IntegrationError(public_message, transient=, retry_after=)` (`src/angee/integrate/errors.py:25`) is the only
  exception an operator sees; `RequestNotSentError` marks safe retries.
- `LiveSession` (`src/angee/integrate/session.py:60`) owns the wake loop, QR reporting and cooperative stop;
  `session_store_path` (`src/angee/integrate/live.py:100`) owns the store convention.
- The stack rows for the protocol client ([Backend](../../../../../docs/stack.md#backend): the IMAPClient, neonize and telethon rows); parse with
  the library, never a hand-rolled protocol.

## Steps

1. **Create the addon and pick the donor.** Folder `src/angee/<capability>_integrate_<vendor>` (`angee-addon`);
   `[project].dependencies` names the capability/integrate distributions and vendor client
   (`src/angee/messaging_integrate_imap/pyproject.toml`). If the capability has no concrete bridge yet → add it
   there first as a `Bridge` child declared with `inherits="integrate.Integration"` and an `ImplClassField` selector
   (`src/angee/messaging/models.py:1032-1055`), never in the vendor addon. **Owner rule:** the capability owns
   the row, the vendor addon owns one implementation ([Repository Role](../../../../../AGENTS.md#repository-role)).

2. **Register the implementation key in `autoconfig.py`** (IMAP):

   ```python
   SETTINGS = {
       "ANGEE_CHANNEL_BACKEND_CLASSES.imap": "angee.messaging_integrate_imap.backend.ImapChannelBackend",
   }
   ```

   The dotted key merges into the capability's registry named by the base class `registry_setting`
   (`src/angee/messaging/backends.py:232`); `ImplClassField.check()` validates it. Seed the vendor row under
   `[resources] master` with `adopt = "slug"`. **Owner rule:** the registry setting and `ImplClassField` own
   selection ([stack](../../../../../docs/stack.md#backend) "Implementation-registry choice"); no addon-local lookup table.

3. **Write the adapter** (IMAP, abbreviated; `src/angee/messaging_integrate_imap/backend.py:186-260,407-419`):

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
   `reconcile_interval`, `absence_threshold`, `tombstone_retention`, `snapshot`, `authoritative`, `config`;
   `src/angee/integrate/states.py:101-115`). `extract` returns one page and the cursor that covers it; the
   driver commits both. If the source is a mutable replica → return `RecordChange` rows and implement
   `enumerate_keys`, `read_keys` (set `supports_identity_reads = True`) and, for two-way, `local_changes` +
   `write_back` (`src/angee/integrate/impl.py:110,141,172,177`). If the capability base already applies →
   inherit `apply_record`; otherwise apply through the domain's idempotent ingest verb and return
   `ApplyResult(target=...)` (`src/angee/messaging/backends.py:273-280`). **Owner rule:**
   [Integration and worker contracts](../../../../../docs/backend/guidelines.md#integration-and-worker-contracts): the cursor commits with the records it
   covers, the driver alone promotes links, adapters never persist cursors or credentials.

4. **Credentials and OAuth.** Read material through the row: `self.bridge.fresh_credential()`, then
   `credential.auth_headers()` or the handler's secret (`src/angee/integrate/models.py:2068,1297`); kinds are
   `CredentialKind` handlers (`src/angee/integrate/credentials.py:17`). If the vendor needs an OAuth preset →
   subclass `OAuthProviderType` with a unique `key` and contribute
   `"ANGEE_OAUTH_PROVIDER_TYPE_CLASSES.<key>"` (`src/angee/integrate/README.md:91-99`, `tests/integrate_fixtures.py:98-113`). If the
   connection must locate a remote resource first → `requires_connection_discovery = True` with
   `discover_connection`/`apply_discovery` (`src/angee/integrate/impl.py:41-62`, `src/angee/integrate/README.md:101-103,133-147`).
   **Owner rule:** integrate persists credentials and the discovery fence; adapters return facts (`src/angee/integrate/README.md:142-147`).

5. **Live sessions (only for a long-lived connection).** Subclass `LiveBridgeImpl`: set `session_queue` (a
   dedicated queue; the stack runs it on the threads pool), `session_class` (a worker-only `LiveSession`
   subclass), and `session_isolation = "process"` when the vendor SDK can abort the interpreter
   (`src/angee/integrate/impl.py:186-191`; fixture: `tests/integrate_fixtures.py:75-78`). The capability's row
   sets `live_impl_field` to its selector (`src/angee/messaging/models.py:1049`). `start_live` enqueues
   `integrate.run_bridge_session` with `expires` of one tick (`src/angee/integrate/impl.py:206-224`); the
   reconciler `ensure_bridge_sessions` restarts healthy connected bridges (`src/angee/integrate/tasks.py:84`).
   **Owner rule:** [Task pitfalls](../../../jobs/skills/jobs-tasks/references/pitfalls.md)
   "A long-lived Celery task needs the three-check wake loop": `LiveSession` owns it; a session never writes `lifecycle` ([Integration and worker contracts](../../../../../docs/backend/guidelines.md#integration-and-worker-contracts) "A lifecycle column is declared intent").

6. **Errors and operator messages.** Raise an `IntegrationError` subclass composed from facts the operator owns
   (`src/angee/messaging_integrate_imap/backend.py:91-98`); pass `transient=`/`retry_after=` for throttling.
   Record-level refusals raise `SemanticError` (quarantined, page continues); infrastructure errors propagate
   (page rolls back) (`src/angee/integrate/impl.py:122-130`). **Owner rule:** [Integration and worker contracts](../../../../../docs/backend/guidelines.md#integration-and-worker-contracts)
   "An integration failure reaches the operator only as an IntegrationError".

7. **Test the bridge.** Transport and cursor logic against an in-memory client via the `client_class` seam
   (`tests/test_messaging_imap.py:637,742`); the driver path with `make_integration(..., model=Channel,
   backend_class="imap")` and `current_stream(bridge, "messages", partition)` (`tests/conftest.py:336,380`), then
   `open_stream`/`advance_stream` or `Channel.run_sync` onto real tables (`tests/test_messaging_imap.py:1-10`).
   Generic contract tests use `MemoryAdapter`/`ReadKeysAdapter` (`tests/test_integrate_streams.py:84,155`) and the
   `ConnectionBridge` fixture registered under `ANGEE_TEST_CONNECTION_CLASSES` (`tests/settings.py:146`). Live
   loops use `FakeLiveSession` (`tests/test_integrate_live.py:46`). Install `angee.integrate.testing` after
   `angee.integrate` in a bare host (`src/angee/integrate/testing/__init__.py`). **Owner rule:**
   [source-addon test models](../../../../../docs/checks.md#source-addon-test-models) own concrete adoption.

8. **Expose operator surfaces through the owners.** Connection test, pause/resume/disconnect, resync and
   conflict resolution already exist on the console schema and `Streams` views (`src/angee/integrate/README.md:15`); a vendor
   addon adds only vendor-specific verbs on the donor row (`src/angee/messaging_integrate_imap/models.py:22`)
   through `angee-graphql` and `angee-pages`.

## Do not

- A private cursor table, checkpoint column or `last_synced_at` on the bridge: `SyncStream.cursor` and its generation own position ([Integration and worker contracts](../../../../../docs/backend/guidelines.md#integration-and-worker-contracts) "The cursor commits with the records it covers").
- Persisting the cursor, promoting a link or writing credential material inside an adapter: the driver and credential manager own those (`src/angee/integrate/impl.py:122-130`, `src/angee/integrate/README.md:36,142-147`).
- Choosing a winner on divergent edits or a remote tombstone against a local edit: conflicts await explicit resolution ([Integration and worker contracts](../../../../../docs/backend/guidelines.md#integration-and-worker-contracts) "Compare both mapped sides with their pinned applied base").
- Running `extract`/`enumerate_keys` inside an enclosing transaction, or network IO inside `prepare_page`/`apply_record`: extraction runs outside the page transaction and may read the row (the IMAP adapter reloads credentials per page through `fresh_credential`, `src/angee/messaging_integrate_imap/backend.py:220`, `src/angee/integrate/models.py:2068`); the driver refuses extraction in an atomic block (`src/angee/integrate/streams.py:440`) and application does database work only (`src/angee/integrate/impl.py:106,122,150`; [Integration and worker contracts](../../../../../docs/backend/guidelines.md#integration-and-worker-contracts) "The cursor commits with the records it covers").
- Re-raising a vendor exception's text or passing `str(exc)` to the operator ([Integration and worker contracts](../../../../../docs/backend/guidelines.md#integration-and-worker-contracts) "An integration failure reaches the operator only as an IntegrationError").
- A `LiveBridgeImpl` without `session_queue`, which would put a session that outlives a tick on the shared prefork queue: `start_live` refuses it (`src/angee/integrate/impl.py:206-218`; [Task pitfalls](../../../jobs/skills/jobs-tasks/references/pitfalls.md) "A long-lived Celery task needs the three-check wake loop").
- A worker writing `Integration.lifecycle` to report an outcome: report on `runtime_status` and the live desire ([Integration and worker contracts](../../../../../docs/backend/guidelines.md#integration-and-worker-contracts) "A lifecycle column is declared intent").
- A second scheduler, polling loop or `models_with` registry for bridges: `integrate.sync_due_bridges` and `ensure_bridge_sessions` own dispatch (`src/angee/integrate/tasks.py:60,84`; [Integrate pitfalls](references/pitfalls.md) "Bridge scheduling follows the final MTI kind").

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

- Declaration knobs `cursor`, `reconcile_interval`, `absence_threshold`, `tombstone_retention`, `config` seed the first generation only; a changed `direction`/`snapshot` opens a new baseline and a changed `kind` is refused (`src/angee/integrate/README.md:56-65`).
- A declared snapshot without `reconcile_interval` closes after its baseline; a missing interval alone never means read-once ([Integration and worker contracts](../../../../../docs/backend/guidelines.md#integration-and-worker-contracts) "Read-once requires a snapshot source and closure").
- Advisory locks are process-local on SQLite: `claim_account` is serialized by the lock, not the database (`src/angee/integrate/impl.py:238-259`, `src/angee/jobs/locks.py:207`).
- A child capability's second intent axis (live desire) is unset by generic lifecycle verbs; the reconciler selects on lifecycle and closes it ([Integrate pitfalls](references/pitfalls.md) "An Integration child's second intent axis").
- A latching `runtime_status=ERROR` gate must be cleared by the repair verb itself ([Integrate pitfalls](references/pitfalls.md) "A latching gate needs a reset").

## Read next

- [Integrate pitfalls](references/pitfalls.md)
- [Integration and worker contracts](../../../../../docs/backend/guidelines.md#integration-and-worker-contracts)
- [Integrate README](../../README.md), [Stack: backend](../../../../../docs/stack.md#backend) rows 101-112
- Skills: `angee-addon`, `angee-models` (the donor row), `jobs-tasks` (queues, budgets), `angee-resources` (the vendor row), `angee-tests`, `slice`

## Sources

<!--
Docs: src/angee/integrate/README.md (whole); docs/backend/guidelines.md sections Record sync, Integrations and workers, Integration and worker contracts (cited by anchor and bullet title); docs/stack.md #backend (messaging-protocol rows and the implementation-registry choice); docs/checks.md #python-and-composition-contracts, #source-addon-test-models, #composition-and-schema, #frontend-packages-and-addon-fragments, #browser-verification, #what-ci-actually-runs; AGENTS.md (Repository Role).
Code: src/angee/integrate/{impl.py,states.py:95-160,streams.py (def list),models.py (def list; 2754-2830),session_process.py:1-80,live.py:1-80,session.py:1-60,session_runner.py:1-50,sync_runner.py:1-40,tasks.py:1-120,scheduler.py:1-80,queue.py,constants.py,autoconfig.py,pyproject.toml,credentials.py (def list),errors.py (def list),discovery.py (def list),locks.py (def list),testing/*};
src/angee/messaging/backends.py:1-330; src/angee/messaging/models.py:1032-1075; src/angee/messaging_integrate_imap/{pyproject.toml,autoconfig.py,README.md,backend.py:1-260,405-420,781-800,models.py:1-80};
src/angee/platform_integrate_vcs/pyproject.toml; tests/{integrate_fixtures.py,conftest.py:336-400,settings.py:140-149,stream_adapters.py:1-45,test_integrate_streams.py:1-100,test_messaging_imap.py:1-120,test_integrate_live.py:1-40}.
Doc gaps: no docs/ walkthrough for writing an adapter; the contract is in impl.py docstrings and the README; derived from IMAP and the test adapters.
Mismatches: platform_integrate_vcs is a catalog tier, not a bridge (its bridge is integrate_vcs.VcsBridge); src/angee/integrate/README.md:8-15 links owner modules by bare filename while docs/ link with paths.
-->
