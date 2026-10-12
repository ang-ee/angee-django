# Provenance

The addon stores attribution for logical writes. Addons composing history declare
provenance as a dependency; the composer emits its concrete
[`Changeset`](models.py). The [core boundary](../../../angee/base/changesets.py)
owns context and commit-aware persistence, without granting an authorization bypass.

The [permission schema](permissions.zed) owns direct access. Other actor types
contribute additive permission fragments from their own addons. Attribution never
grants access to touched records; history reads follow those records' permissions.

Storage follows Django's swappable-model convention, with the default owned by
[base autoconfig](../../../angee/base/autoconfig.py). Consumer history-FK migrations
must depend on `migrations.swappable_dependency(settings.ANGEE_CHANGESET_MODEL)`.
Django's `makemigrations` owns the initial schema; see the
[runtime migration policy](../../../docs/composer.md#addon-owned-runtime-migrations).

## Boundaries

Strict mode refuses a captured write without an open boundary
(`ChangesetRequired`). Each entrypoint owns one boundary per unit of work:

| Entrypoint | Boundary | Owner |
|---|---|---|
| HTTP request | one per request, sync or async, failed on status ≥ 400 or a GraphQL `errors` result | [`ChangesetMiddleware`](../../../angee/base/middleware.py), [`AngeeGraphQLView`](../graphql/views.py) |
| WebSocket connection (GraphQL subscriptions, agent ACP) | one per ASGI connection scope | [`ChangesetScopeMiddleware`](../../../angee/base/middleware.py) via [`angee.asgi`](../../../angee/asgi.py) |
| Celery task | one per task run, `task.<name>` | [`angee.jobs.celery.Task`](../../../angee/jobs/celery.py) |
| Management command | one per command, `manage.<command>`; `serve`, `runserver` and `test` hand it to their requests, connections, tasks and tests | [`angee.compose.manage.main()`](../../../angee/compose/manage.py), called by every rendered `manage.py` |
| Bridge live session | one per session run, in the task or the spawned child | [`run_bridge_session_job`](../integrate/session_runner.py) |
| Separate logical writes | `fresh=True` for merge, revert, seed and MCP tool calls | their owners |

A boundary stores one changeset row per writing thread and actor under its
shared facts: Django connections are per thread, so a worker started with
`copy_context().run` records its own row in its own transaction, and a loop that
changes the ambient actor records one row per actor. All rows close with the
boundary's outcome. A `manage.py` rendered before the framework owned the
entrypoint has no boundary; its first captured write fails naming the remedy
(call `angee.compose.manage.main()`; re-render from the project template).

`Changeset.revert()` restores the state before each row's first write, refusing
any later touch. It authorizes writes on the subjects, restores parents before
MTI children, delegates assertion restoration to `StatementManager.revert`, and
re-runs survivorship. First assertions withdraw; subjects with a trash owner use
retained removal. Without one, retained references block deletion by row. Reverting
the revert restores trash and assertions under another changeset. A completed
remote write remains remote; the next sync observes local differences and uses
its normal outbox/precondition policy.

`Changeset.document()` exports deterministic canonical JSON for a trusted
archive caller under a system boundary. It includes native history rows indexed
by `touched` and excludes its own hash, which closure computes through the shared
hash owner. This is the cold-store seam; no sink, pruning, routing or sealing job
ships here. User-facing history exports must authorize and redact through each
touched subject; direct changeset access does not grant those records' reads.
