# Addon Runtime Migrations

## Summary

Angee composes abstract addon models into concrete downstream Django apps under
`runtime/<app_label>/`. Django owns migrations for those concrete apps, but its
autodetector cannot infer every lossless transition. A renamed field whose type
also changes, for example, is emitted as a remove/add pair and loses existing
data unless a human supplies `RenameField` followed by `AlterField`.

Addons need an append-only way to ship ordinary Django migrations for those
transitions. During explicit `angee build`, the composer will materialize each
applicable source migration into the downstream runtime migration package,
attach it to that project's current migration graph, and then leave all further
planning and execution to Django. The existing provision sequence remains:

1. `angee build` emits runtime sources and materializes applicable addon
   migrations.
2. `makemigrations` sees the manually advanced historical state and generates
   any remaining model changes.
3. `migrate` executes and records the combined graph normally.

The first real use is the `parties.Relationship` transition from
`from_party`/`to_party` to `party`/`other_party`, preserving the existing foreign
key values.

## Goals

- Let an addon ship normal Django `Migration.operations`, including
  `RunPython`, `RunSQL`, `SeparateDatabaseAndState`, rename operations, and
  reversible code.
- Copy the complete source module so local data-migration functions remain
  self-contained in the downstream migration history.
- Attach the migration to each downstream project's actual graph without
  hard-coding a sequence number or project-specific leaf in the addon.
- Apply old migrations only when their exact historical-state precondition
  matches, including when a consumer skips several addon releases.
- Keep materialization deterministic, idempotent, append-only, auditable, and
  fail-fast.
- Keep Django as the owner of migration graph loading, historical state,
  execution, rollback, routing, and `django_migrations` recording.

## Non-goals

- Replacing or post-processing Django's migration autodetector.
- Executing migration operations during `angee build`.
- Generating manual migrations from model diffs.
- Versioning applicability from package versions or a database-side Angee
  ledger. Historical Django `ProjectState` and the committed runtime migration
  graph are the source of truth.
- Rewriting or deleting a migration that has already been materialized.
- Materializing migrations during ordinary Django app population or
  `Runtime.emit_if_stale()`.

## Owner Map

| Fact or behavior | Owner |
|---|---|
| Which manual migrations an addon contributes, their stable names, modules, and target labels | The addon's `addon.toml` |
| The operations, reversibility, local data functions, explicit dependencies, and historical-state applicability | The addon-owned migration module |
| Addon graph order | `AppGraph` and its resolved `AppConfig` order |
| Runtime migration package and current graph | Django `MIGRATION_MODULES`, `MigrationLoader`, and `MigrationGraph` |
| Planning, numbering, dependency resolution, integrity checking, and copying | A composer-owned runtime migration materializer |
| Runtime build/check orchestration | `Runtime` |
| CLI argument parsing and reporting | `angee` management command |
| Operation execution, rollback, routers, transactions, and recording | Django's migration executor |

The materializer is framework code because every addon needs the same seam. The
actual migrations remain addon-owned because only the addon knows the domain
meaning and the lossless transition.

## Addon Contract

An addon declares an ordered array of migrations in `addon.toml`:

```toml
[[migrations]]
name = "relationship_anchor"
app_label = "parties"
module = "runtime_migrations.relationship_anchor"
```

`AddonContract` exposes these as an ordered tuple of immutable
`AddonMigration` declarations:

- `name`: a stable snake-case identifier, unique within the contributing addon;
- `app_label`: the emitted runtime app whose graph receives the migration;
- `module`: a dotted module reference, relative to the contributing addon's
  import package unless already fully qualified.

Source modules must live outside the addon's conventional `migrations` package;
`runtime_migrations` is the recommended package name. Otherwise Django discovers
the source file as an executable migration of the abstract addon app before the
materializer can attach it to the downstream runtime graph.

The global origin is `<addon_config.name>:<name>`, for example
`angee.parties:relationship_anchor`. Declaration order is significant and is
preserved within one addon. Across addons, the already-resolved `AppGraph` order
is authoritative. Duplicate global origins fail configuration.

The declaration is explicit rather than inferred from a directory scan because
target label, stable identity, and order are intent rather than path-derivable
facts. A contributing addon may target an upstream runtime label when its model
extension changes that upstream concrete model.

## Source Migration Module

The declared module is a normal Python migration module:

```python
from django.db import migrations
from django.db.migrations.state import ProjectState


def applies(project_state: ProjectState) -> bool:
    model = project_state.models.get(("parties", "relationship"))
    return model is not None and "from_party" in model.fields


def forwards(apps, schema_editor):
    # Historical models only; no current-model imports.
    Widget = apps.get_model("example", "Widget")
    Widget._base_manager.using(schema_editor.connection.alias).filter(
        legacy_status="ready",
    ).update(status="active")


class Migration(migrations.Migration):
    dependencies = []
    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
```

The module contract is:

- `Migration` must subclass Django's `migrations.Migration`.
- `applies(project_state)` is required, pure, and must return a boolean. It reads
  only the supplied historical `ProjectState`; it must not query a database,
  read the current app registry, or mutate the state.
- `True` means the exact old state is present and the migration must be
  materialized. `False` means the transition is irrelevant or the historical
  state already has the new shape. A hook should raise `ImproperlyConfigured`
  with a precise message when it recognizes an incompatible partial state rather
  than silently returning `False`.
- The operations are ordinary Django operations. Data functions use historical
  models from the `apps` argument and `_base_manager` for system migration work.
- The module must be self-contained or import only stable APIs that will remain
  available for the lifetime of downstream migration history.
- Declarations and modules are append-only. Once released, an origin's source
  bytes are immutable.

The full module is copied, rather than recreating its operations through
`MigrationWriter`, so local `RunPython` functions and authored comments survive
without imports back into a later addon version.

## Dependencies

The materializer automatically adds a dependency on the target app's single
current leaf. The addon therefore does not know the downstream sequence number.
When the target has no migrations, no automatic target dependency is added and
an applicable migration becomes that app's `0001` migration.

Source `Migration.dependencies` otherwise retains normal Django semantics. An
exact dependency such as `("contenttypes", "0002_remove_content_type_name")`
is preserved. For another composed runtime app whose downstream leaf is not
known to the addon, the source may use:

```python
dependencies = [("iam", "__latest__")]
```

Before copying, Angee resolves `__latest__` to that app's single current leaf.
A missing or conflicting referenced leaf fails materialization. The generated
footer transforms only declared `__latest__` entries and appends the automatic
target dependency, preserving other dependency objects such as Django
swappable dependencies.

Materialized migrations for the same target are chained in resolved declaration
order. Planning mutates an in-memory historical state after each applicable
migration, so migrations from several skipped releases become applicable and
materialize sequentially in one build.

## Materialization Flow

`Runtime` invokes a composer-owned materializer only on the explicit build path.
The flow is:

1. Load the existing runtime migration graph with Django's `MigrationLoader`.
2. Discover declarations from installed addon configs in resolved app order.
3. Validate every declaration, source module, target label, origin, dependency,
   and existing materialized origin.
4. Read the graph's historical `ProjectState`.
5. For each declaration not already materialized, call `applies()` against the
   current simulated state.
6. For an applicable migration, resolve dependencies, choose the next numeric
   name for its target, instantiate the source `Migration` with that name and
   target, and call `mutate_state()` on a clone. A state operation that cannot
   apply fails the build before files are written.
7. Continue planning against the mutated state so later declarations see the
   transition.
8. After the full plan validates, write each planned module atomically into
   `runtime/<app_label>/migrations/<number>_<name>.py`.

The command does not run database operations and does not call `migrate`.
`angee provision` already starts each following step in a fresh interpreter, so
its existing `build -> makemigrations -> migrate` ordering is sufficient.

Numbering uses the largest numeric migration prefix already present for the
target and increments it. The single current leaf becomes the dependency; a
target with multiple leaves is already conflicted and fails with an instruction
to merge before rebuilding. A planned migration becomes the leaf for the next
planned migration targeting that app.

## Materialized File And Integrity

The copied source bytes are followed by a deterministic generated footer. In
conceptual form:

```python
# Generated Angee migration materialization metadata.
Migration.dependencies = [
    resolved if dependency == ("iam", "__latest__") else dependency
    for dependency in Migration.dependencies
]
Migration.dependencies.append(("parties", "0012_previous"))
Migration.angee_origin = "angee.parties:relationship_anchor"
Migration.angee_source_sha256 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
```

The actual footer is rendered deterministically and carries no timestamp. The
origin makes builds idempotent independently of downstream numbering. The
digest covers the exact source-module bytes before the footer.

On every build/check, an existing origin is verified:

- its stored source digest must match the currently declared source module;
- the copied pre-footer body must hash to the stored digest;
- one origin may appear in exactly one runtime migration.

A mismatch fails loudly. Angee never overwrites the downstream migration because
it may already be applied in production. Normal project-authored migrations that
have no Angee origin remain outside this integrity check and stay fully owned by
the downstream project.

## Runtime Lifecycle

- `Runtime.emit_if_stale()` remains source-only. Normal server boot never adds
  migration files.
- Explicit `angee build` emits runtime sources when needed and always runs the
  migration materializer, including when model sources themselves have no
  drift. This supports data-only manual migrations.
- `angee build --check` performs the same validation and dry-run planning. It
  fails when at least one migration is applicable but not materialized, without
  writing files.
- `Runtime.clean()` and `Runtime.reset()` continue preserving everything under
  runtime migration directories, including materialized addon migrations.
- Migration files remain excluded from generated-source drift because Django
  and the downstream project own the complete history after materialization.

## Failure Semantics

Materialization fails before planned files are written when:

- a declaration has an invalid or duplicate name/origin;
- the target is not an emitted runtime label;
- the source module, `Migration`, or `applies` contract is invalid;
- `applies` raises or returns a non-boolean;
- a target or `__latest__` dependency has conflicting leaves;
- a declared dependency cannot be resolved;
- an operation cannot advance the historical state;
- the computed output filename collides with an unrelated file;
- an existing origin is duplicated, edited, or differs from its released source;
- the materialized migration graph would be inconsistent.

Messages name the contributing addon, stable migration name, target app, and
offending module or graph node. No fallback to lossy autodetection is allowed
after a declared applicable migration fails.

## First Acceptance Migration: Parties Relationship

`angee.parties` will declare `relationship_anchor`, targeting the `parties`
runtime app. Its source module applies only when the historical
`parties.Relationship` has `from_party` and `to_party` and does not yet have
`party` and `other_party`. An absent model or the complete new shape returns
`False`; recognized partial mixtures raise.

Its operations will:

1. remove the old relationship constraints that refer to the old field names;
2. rename `from_party` to `party`;
3. rename `to_party` to `other_party`;
4. alter `other_party` to nullable `SET_NULL` while preserving its column data;
5. add `other_name` with the current default;
6. change model ordering to `party`, then `sqid`;
7. add the new partial uniqueness and counterparty check constraints.

Existing rows retain both foreign keys. Because every old `to_party` value is
non-null, the new "tracked party or free-text name" constraint is satisfied
without a data backfill. After materialization, Django's autodetector should
produce no destructive remove/add operations for these fields.

## Tests

Focused tests will cover:

- parsing ordered migration declarations from `addon.toml`;
- relative and fully qualified source module resolution;
- copying a complete migration containing a local `RunPython` function;
- attachment to the current leaf and sequential numeric naming;
- `__latest__` cross-app dependency resolution;
- historical-state application across multiple pending migrations;
- fresh/current-state skips and incompatible-state failures;
- idempotent rebuilds;
- dry-run `angee build --check` behavior;
- source and copied-body digest drift;
- duplicate origins, invalid modules, invalid targets, leaf conflicts, and
  filename collisions;
- preservation through runtime reset/clean;
- the parties transition advancing an old `ProjectState` to the current
  `Relationship` state without `RemoveField`/`AddField` data loss;
- a second Django autodetection pass producing no remaining parties relationship
  change after the manual migration state is applied.

The relevant existing addon and composer suites remain the regression baseline.
Before completion, run the focused tests, the full backend test suite, Ruff,
Mypy, and the runtime build check required by the repository guidelines.

## Documentation

The addon contract docstrings are the API source of truth. `docs/composer.md`
will explain that addons may contribute manual runtime migrations, that explicit
build materializes them before `makemigrations`, and that normal boot never does.
`docs/backend/guidelines.md` will record the append-only/self-contained migration
rule and the historical-model requirement for data migrations. No parallel
inventory of individual migrations will be maintained in prose.
