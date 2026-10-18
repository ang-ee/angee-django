# Provenance

The addon stores attribution for logical writes. Addons composing history declare
provenance as a dependency; the composer emits its concrete
[`Changeset`](models.py). The [core boundary](../../../angee/changesets.py)
owns context and commit-aware persistence, without granting an authorization bypass.

The [permission schema](permissions.zed) owns direct access. Other actor types
contribute additive permission fragments from their own addons. Attribution never
grants access to touched records; history reads follow those records' permissions.

Storage follows Django's swappable-model convention, with the default owned by
[base autoconfig](../../../angee/autoconfig.py). Consumer history-FK migrations
must depend on `migrations.swappable_dependency(settings.ANGEE_CHANGESET_MODEL)`.
Django's `makemigrations` owns the initial schema; see the
[runtime migration policy](../../../docs/composer.md#addon-owned-runtime-migrations).

## Boundaries

Strict mode refuses a captured write without an open boundary
(`ChangesetRequired`). Each entrypoint owns one boundary per unit of work:

| Entrypoint | Boundary | Owner |
|---|---|---|
| HTTP request | one per request, sync or async, failed on status ≥ 400 or a GraphQL `errors` result | [`ChangesetMiddleware`](../../../angee/middleware.py), [`AngeeGraphQLView`](../graphql/views.py) |
| WebSocket connection (GraphQL subscriptions, agent ACP) | one per ASGI connection scope | [`ChangesetScopeMiddleware`](../../../angee/middleware.py) via [`angee.asgi`](../../../angee/asgi.py) |
| Celery task | one per task run, `task.<name>` | [`angee.jobs.celery.Task`](../../../angee/jobs/celery.py) |
| Management command | one per command, `manage.<command>`; `serve`, `runserver` and `test` hand it to their requests, connections, tasks and tests | [`angee.compose.manage.main()`](../../../angee/compose/manage.py), called by every rendered `manage.py` |
| Bridge live session | one per session run, in the task or the spawned child | [`run_bridge_session_job`](../integrate/session_runner.py) |
| Separate logical writes | `fresh=True` for merge, revert, seed and MCP tool calls | their owners |
| Test | one per test, `pytest` | the `captured_changeset` fixture of the [`angee.testing.fixtures`](../../../angee/testing/fixtures.py) plugin every test host loads |

A boundary stores one changeset row per writing thread and actor under its
shared facts: Django connections are per thread, so a worker started with
`copy_context().run` records its own row in its own transaction, and a loop that
changes the ambient actor records one row per actor. All rows close with the
boundary's outcome. An entrypoint that owns the whole process (a management
command, a test) opens its boundary with `process=True`, so a plain thread or
pool it starts without copying the context writes under it too, in its own row;
concurrent entrypoints (requests, connections, tasks) never publish one, and their
threads carry the context explicitly. A `manage.py` rendered before the framework owned the
entrypoint has no boundary; its first captured write fails naming the remedy
(call `angee.compose.manage.main()`; re-render from the project template).

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
used by Comments; the web `HistoryEntrySummary` component renders that compact
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

`revert_changeset(model, id, changeset)` is the `ActionResult` verb behind the
History tab's Revert: the record's `history_revert_permission` is the one
admission rule and `Changeset.revert()` restores. Resource metadata marks a model that keeps history with
`history: true`, the fact [`web/`](web/) uses to show the tab.

[`web/`](web/) contributes the History tab once, at `form#sections`, visible on
every saved record whose resource keeps history, and exports
`RecordHistoryTimeline` (the timeline with its read, paging and Revert; `compact`
renders one line per changeset linking to the tab) and `useRecordHistory` for
pages that count or embed the same entries, such as integrate's link history
drill-down and the notes example's activity chatter.

`Changeset.document()` exports deterministic canonical JSON of a closed changeset
for a trusted archive caller under a system boundary. It includes native history
rows indexed by `touched` and excludes its own `hash`, which this export computes
through the shared hash owner and records on first export; closing a boundary
writes one bookkeeping row and never re-reads its history. This is the cold-store
seam; no sink, pruning, routing or sealing job ships here. User-facing history exports must authorize and redact through each
touched subject; direct changeset access does not grant those records' reads.
