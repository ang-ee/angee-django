# Provenance

The addon owns attribution, capture, replay and typed statement engines alongside
Angee permission, identity and serving policies. Addons opting into these capabilities declare `angee.provenance`
as a dependency and compose its model mixins. The model foundation has no
provenance dependency.

[`context.py`](context.py), [`history.py`](history.py) and [`querysets.py`](querysets.py)
own logical boundaries, capture and querysets directly. The remaining
[`engine/`](engine/) modules hold storage, replay and plain-Django extension
contracts used by the isolated swappable-model host. Historical Angee imports
resolve directly to the conventional addon modules.
The context owner keeps commit-aware persistence.
Its native transaction adapter tracks the identity of an `on_commit` callback
in Django's callback queue: savepoint rollback removes that callback, while
release retains it. This lets the next captured write recreate a rolled-back
attribution row without querying it on every write. The public `on_commit`
notification alone cannot distinguish those states before the outer commit.
[`history.py`](history.py) and [`querysets.py`](querysets.py) extend Django and
simple-history's native model, manager and signal contracts. They do not import
Angee model or permission policy. The explicit
[`contrib/angee/`](contrib/angee/) adapters supply guarded writes, replay and
Angee document scopes. Angee models import `HistoryMixin` from
`angee.provenance.contrib.angee.history` and compose a captured manager when they
declare a custom queryset. [`models/changesets.py`](models/changesets.py) adapts native abstract storage;
[`models/principals.py`](models/principals.py) owns rule and seed principals.
[`managers.py`](managers.py) adapts native claims and
[`changeset_querysets.py`](changeset_querysets.py) adds protected document/merge identities. No captured write
grants an authorization bypass.

[`statements.py`](statements.py) owns Angee's retained assertion models, source
trust, admission and replay. Its defaults compose Angee identity, REBAC,
document ownership, merge and evidence contracts. These are addon integration
APIs; the independently usable native capture boundary is `context`, `history`
and `querysets`. IAM principal participation comes from the optional
[`UserSource` donor](contrib/iam/models.py), keeping IAM independent of provenance.


The [permission schema](permissions.zed) owns direct access. Other actor types
contribute additive permission fragments from their own addons. Attribution never
grants access to touched records; history reads follow those records' permissions.

Runtime storage is selected by `ANGEE_CHANGESET_MODEL`, with the default
owned by [addon autoconfig](autoconfig.py). It is also Django's historical
swappable-migration identity. Do not set the retired
`PROVENANCE_CHANGESET_MODEL` anywhere, including through
`YAMLCONF_PROVENANCE_CHANGESET_MODEL`: system check `provenance.E005` treats even
its presence as an error. The composer fingerprint changes with this setting
contract, so a stack recomposes after integration.
Existing consumer history-FK migrations
must depend on `migrations.swappable_dependency(settings.ANGEE_CHANGESET_MODEL)`.
Django's `makemigrations` owns the initial schema; see the
[runtime migration policy](../../../docs/composer.md#project-migration-history).

## Boundaries

Strict mode refuses a captured write without an open boundary
(`ChangesetRequired`). Each entrypoint owns one boundary per unit of work:

| Entrypoint | Boundary | Owner |
|---|---|---|
| HTTP request | one per request, sync or async, failed on status ≥ 400 or a GraphQL `errors` result | [`ChangesetMiddleware`](middleware.py), the [native GraphQL operation extension](graphql.py) |
| WebSocket connection (GraphQL subscriptions, agent ACP) | one per ASGI connection scope | [`ChangesetScopeMiddleware`](middleware.py) via the [GraphQL Channels contribution](../graphql/asgi.py) |
| Celery task | one per task run, `task.<name>` | [`angee.provenance.tasks.Task`](tasks.py) |
| Management command | one per command, `manage.<command>`; `serve`, `runserver` and `test` hand it to their requests, connections, tasks and tests | `angee.server.management`'s `ANGEE_SERVER_COMMAND_CONTEXT`, contributed by [provenance](contrib/angee/context.py) |
| Bridge live session | one per session run, in the task or the spawned child | [`run_bridge_session_job`](../integrate/session_runner.py) |
| Separate logical writes | `fresh=True` for merge, revert, seed and MCP tool calls | their owners |
| Test | one per test, `pytest` | the `captured_changeset` fixture of the [`angee.provenance.testing.fixtures`](testing/fixtures.py) plugin provenance test hosts load explicitly |

`current_changeset()` is optional; `required=True` refuses unavailable storage.
The configured storage owner supplies actor, reason and strict-mode policy.
`changeset(using=alias)` binds a unit of work to that database and refuses a
conflicting capture; an unbound boundary records on each actual capture destination.
`PROVENANCE_POLICY` selects the native boundary adapter, defaulting to
[`AngeePolicy`](contrib/angee/policy.py). It resolves SubjectRef principals and
retains real REBAC operation gates; classification or attribution grants no authority.
Angee uses native capture-only mixins so its record and trash owners retain removal
admission. Its explicit storage/document callbacks preserve historical tables,
column identity, JSON bytes and in-place current statement row identities.
The addon retains its simple-history fork dependency for the native
`get_instance(using=...)` snapshot-routing contract; the generic engine also
supports plain simple-history independently.

A boundary stores one changeset row per writing thread and actor under its
shared facts: Django connections are per thread, so a worker started with
`copy_context().run` records its own row in its own transaction, and a loop that
changes the ambient actor records one row per actor. All rows close with the
boundary's outcome. An entrypoint that owns the whole process (a management
command, a test) opens its boundary with `process=True`, so a plain thread or
pool it starts without copying the context writes under it too, in its own row;
concurrent entrypoints (requests, connections, tasks) never publish one, and their
threads carry the context explicitly. Management command attribution is contributed through `ANGEE_SERVER_COMMAND_CONTEXT`.
The rendered `manage.py` calls `angee.server.management.execute`, which enters
those hooks after setup. Re-render an obsolete entrypoint from the project template.

`Changeset.revert()` restores the state before each row's first write, refusing
any later touch. It authorizes writes on the subjects, restores parents before
MTI children, replays statement rows through the statement tables' own history
owner (`StatementHistory`, a `ModelHistory`), and re-runs survivorship. First assertions withdraw; subjects with a trash owner use
retained removal. Without one, retained references block deletion by row. Reverting
the revert restores trash and assertions under another changeset. A completed
remote write remains remote; the next sync observes local differences and uses
its normal outbox/precondition policy.

## History surface

[`schema.py`](schema.py) owns the read surface in the `console` schema. The
`history(model, id, limit, offset)` query returns one record's changesets newest
first (`RecordHistoryType { total, entries: [HistoryEntryType!]! }`), resolved through the record's own
`read` gate: a hidden or unknown record reads as `null`, and a model without
native history or a REBAC resource type is refused. `HistoryAttribution` is the
shared interface for detailed entries and the compact `HistorySummaryType` lines
used by Activity; the web `HistoryEntrySummary` component renders that compact
shape. Each `HistoryEntryType` carries the changeset's attribution (`actor` as
the public SubjectRef, `actor_model`/`actor_id` as the same public reference,
`source_kind`), `action`, `activity`, `change_reason`,
times, `outcome`, the field deltas simple-history's `diff_against` computes
between the record's versions in that changeset and the row before them (a first
version diffs against an empty snapshot; references carry public ids), the
record's statement versions the changeset wrote, the survivor and absorbed
records of a merge, and `can_revert`. Deltas and statement values leave out the
fields the actor cannot read on the record (`read__<field>`): statement tables
mirror history tables, so the subject's redaction is the only rule. MTI parents'
versions of the same row join the entry. A page holds at most 100 changesets.

A null `changes` or `statements` projection means its retained source values are
unavailable; an empty list remains a truthful empty projection. Attribution and
independently complete details remain visible with one warning on the entry.
A delta also needs its predecessor: a complete current version with an incomplete
predecessor has null changes and cannot be reverted. Merge identities use retained
primary keys, deletion/redirect facts and capture metadata, which the former
changeset collision did not overwrite.

`revert_changeset(model, id, changeset)` is the `ActionResult` verb behind an
Activity history entry's Revert: the record's `history_revert_permission` is the one
admission rule and `Changeset.revert()` restores. Resource metadata marks a model that keeps history with
`history: true`, the fact [`web/`](web/) uses to read entries. A resource whose
`StatementsMixin` declares an aspect with subject fields has `fieldProvenance: true`;
the web mounts source marks for those fields even when the resource has no native history.
Only a resource with history can open a changeset pane from a mark.

[`web/`](web/) contributes the `provenance.change` Timeline kind and the history drill-in, visible on every
saved record whose resource keeps history, and exports `RecordHistoryTimeline`
(the standalone timeline with its read, paging and Revert; `compact` renders one
line per changeset linking to Activity) and `useRecordHistory` for
pages that count or embed the same entries, such as integrate's link history
drill-down and the notes example's Activity shortcut.

Angee historical bases compose native `HistoryAttribution`: `history_changeset`
attributes capture independently of copied source fields, including a source
`changeset`. `history_source_complete` distinguishes retained source state from
legacy values lost by the former shared-column capture. Lossless export, diffs
and replay refuse incomplete snapshots. ModelHistory requires
`use_base_model_db=True`; actual table aliases retain source identity without
adding source_database columns. Hosts still carrying the prior historical schema
must rename the old historical changeset field to history_changeset and recreate
copied source changeset fields
where declared. Mark legacy projections complete only when their source values
were retained; never backfill lost values from current state or attribution.
The [capture transition](docs/ENGINE.md)
owns that migration contract. Historical Python imports remain compatibility
aliases to these native owners. Statement last-writer attribution remains its
separate source contract.

The engine code was folded back from the former `django-provenance` library at commit
`9be73feaf71e14e2fd1315745854fee6ec48b56e`. The addon is the sole app with
label `provenance` and ships no migrations. Each composed project owns its native
`runtime/provenance/migrations` history through django-compose's `MIGRATION_MODULES`
routing. The library app was never installed in those projects, and their runtime
migrations do not import it, so existing databases need no migration or table
change for this code move. The [engine contracts](docs/ENGINE.md) retain the
library's detailed capture, replay and statement behavior.

`Changeset.document()` exports deterministic canonical JSON of a closed changeset
for a trusted archive caller under a system boundary. It includes native history
rows indexed by `touched` and excludes its own `hash`, which this export computes
through the shared hash owner and records on first export; closing a boundary
writes one bookkeeping row and never re-reads its history. This is the cold-store
seam; no sink, pruning, routing or sealing job ships here. User-facing history exports must authorize and redact through each
touched subject; direct changeset access does not grant those records' reads.

GraphQL attribution composes Strawberry's native operation extension through
`ANGEE_GRAPHQL_EXTENSION_FACTORIES`. It joins an existing request or connection
boundary; direct schema and MCP tool execution opens its own boundary. The
factory receives Strawberry's sync flag so asynchronous teardown uses the
changeset owner's thread-sensitive close path. Handled execution errors mark the
boundary failed while preserving successful statements.

`Aspect[Subject]` and `StatementRecords[Subject]` retain the native owning-model
callback type. Model-bound candidate methods keep their specific subject
signatures; a declaration can combine them with ordinary callback-free aspects.
The protected apply callback retains its existing keyword member/value contract.
