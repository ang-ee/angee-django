# Migrations and runtime

Migration and runtime procedures moved from the backend guide. The policy and
contracts stayed there: [Migration history](../../../../docs/backend/guidelines.md#migration-history),
[Persistence primitives](../../../../docs/backend/guidelines.md#persistence-primitives),
[Development server](../../../../docs/backend/guidelines.md#development-server) and
[Addon-owned runtime migrations](../../../../docs/composer.md#addon-owned-runtime-migrations).
Commands live in [Composition and schema](../../../../docs/checks.md#composition-and-schema).

- **Domain renames need an explicit upgrade path**
  ([Migration history](../../../../docs/backend/guidelines.md#migration-history)).
  Describe which old state needs data migration and which reconciliation
  follows it. Consumers still carrying the historical `social/*` permission
  namespace need a planned transition to `posts/*`; verify their actual data and
  migration state before selecting the migration and the prune-stale
  permission sync ([Composition and schema](../../../../docs/checks.md#composition-and-schema)).
  A fresh installation does not inherit an old deployment's repair procedure.
- **Retiring a Python owner that old migrations import** is part of a history
  reset ([Migration history](../../../../docs/backend/guidelines.md#migration-history)):
  retire those imports through the supported migration path, inspect
  non-composed histories and pinned addons, and state which older releases must
  cross the bridge first.
- **A schema change leaves PostgreSQL planner statistics behind.** An added or
  altered column has none until `ANALYZE`, and autovacuum analyzes on row churn,
  not schema changes. `manage.py provision` analyzes the tables of every app it
  migrates; follow a manual `migrate` with `ANALYZE` of the changed tables.
- **Row updates followed by DDL on the same tables need immediate constraints.**
  PostgreSQL queues deferred foreign-key checks for rows a migration updates and
  refuses a later `ALTER TABLE` or index build in the same atomic migration.
  Run `SET CONSTRAINTS ALL IMMEDIATE` (PostgreSQL only) before the data step.
  SQLite never shows this; rehearse such a migration against populated PostgreSQL.
- **A structural marker consumed after runtime emission must be emitted too.**
  A non-inherited `__dict__` source-model marker stops at the abstract source unless
  the composer carries it into the concrete runtime class body
  ([Rules](../../../../docs/backend/guidelines.md#rules), model markers).
- **Regenerate the SDL after rebuilding the runtime.** Serving never emits it
  ([Development server](../../../../docs/backend/guidelines.md#development-server));
  rerun native compose and the schema check from
  [Composition and schema](../../../../docs/checks.md#composition-and-schema). A
  missing `runtime/schemas/*.graphql` makes Vite ENOENT and the SPA silently
  fails to mount (every e2e fails at list load) while `:5173` still returns 200;
  check `runtime/schemas/` before chasing app/test regressions.
- **Data migrations access REBAC-scoped models through `_base_manager`, and
  backfills need a rows-present proof.**
  [Migration history](../../../../docs/backend/guidelines.md#migration-history)
  owns the manager rule (iam's `UserManager` inherits `use_in_migrations = True`
  from Django's, so `objects` inside a `RunPython` raises `MissingActorError`
  under strict mode). A fresh-DB `migrate` never executes a row-dependent
  backfill body: prove backfills against a database that has rows (the agents
  service-user backfill failed only on live dev DBs for this reason).
- **A restricted `makemigrations` invocation must cover every changed concrete
  app.** Derive labels from the composed model registry instead of copying an
  old example's label list. Missing a changed app's migrations can leave its
  tables absent when resources load. Use [checks](../../../../docs/checks.md) for the host
  lifecycle sequence and execution root.
