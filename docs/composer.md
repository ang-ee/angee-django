# The Composer

The composer is Angee's Django composition layer. It turns a project's normal
Django settings contract and installed addon apps into one runnable Django
project: final settings, a single ordered app registry, generated concrete ORM
models and a shared generated-file lifecycle. Angee's host and installed
capabilities supply URL/ASGI defaults and their build integrations.

A project declares root apps with `INSTALLED_APPS`. An addon is a plain Django
app whose `addon.toml` owns its identity, metadata, dependencies, Python
requirements, and explicit capability declarations. Django owns AppConfig
selection, labels, paths, model registration and `ready()`. Fields and executable
behavior remain in Python. Conventional modules provide defaults; explicit
manifest declarations take precedence.

This page maps the flow and ownership boundaries. Current API details, default
lists, validation rules, and exact declaration shapes live in the owning modules,
classes, and docstrings linked below.

## Flow

A normal project declares `INSTALLED_APPS` and overrides in `settings.yaml`.
Addon defaults and derived settings remain with their addons; native yamlconf
merges the ordered contributions. Deployment settings and secrets stay late-bound.

1. **Explicit prebuild** — native `uv sync` invokes the host's Hatch hook for
   editable development; `uv build` creates the project-specific distribution.
   The hook selects `ProjectBuildConfig` in an isolated process. Native source
   `import_models()` hooks run once, then the compiler emits and imports ordinary
   concrete modules before any `ready()` callback. Builds never provision a DB.
2. **Serving startup** — Django imports generated `runtime.settings`, which calls
   the same project settings loader. The host’s native `AngeeConfig` validates the
   built ordered native app identities, then imports prebuilt concrete modules
   during phase 2. It never scans source declarations, renders, writes or repairs.
   A changed app closure requires rebuilding, including plain or donor-only apps.
3. **Native history authoring** — after an editable build, `makemigrations` binds
   authored addon upgrades and creates native history beside generated models in tracked
   `runtime/<label>/migrations/` packages. Review and commit it before building a distribution.
4. **Provisioning** — the installed prebuilt project runs one native Django
   process for read-only history drift validation, migrate, policy sync and
   declared addon commands. Production installation uses `uv sync --no-editable`.

`uv run --no-sync python manage.py angee check` explicitly checks build drift
without writing. It is not an automatic serving-time scan. Missing prebuilt
output fails clearly; installation through the Hatch hook creates the first artifact.

## Owner Map

| Concern | Owner |
|---|---|
| Project-root discovery and project settings/bootstrap environment | [`ProjectContract`](../angee/conf.py), called by generated `runtime.settings` |
| Bounded project YAML loading and accepted source provenance | [`ProjectContract`](../angee/conf.py) |
| Native YAML settings merge and source inspection | [`angee.utils.yamlconf`](../angee/utils/yamlconf.py) |
| Overridable framework defaults and ordered always-on core apps | [`angee.defaults`](../angee/defaults.py) |
| Reserved composed settings and final settings mutation | [`AutoConfig`](../angee/utils/autoconfig.py) owns reserved names; [`Composer`](../angee/compose/composer.py) assigns their values |
| Django app discovery, identity aliases and root annotations | [`AppGraph`](../angee/compose/appgraph.py) |
| Dependency ordering and cycle rejection for both discovery paths | `hatch_angee.order_app_dependencies` |
| App settings fragments and declared host env overlays | [`AutoConfig`](../angee/utils/autoconfig.py) |
| Host manifest adapter | [`ManifestDeclarations`](../angee/build.py) implements [`AppDeclarationSource`](../angee/compose/declarations.py), selected by `COMPOSE_APP_DECLARATIONS` before app resolution |
| Addon declarations and parsing | `addon.toml` and hatch-angee's native `AddonManifest`; [`angee.addons`](../angee/addons.py) binds the result to a native config |
| Abstract-source discovery, donor order, parent relationships and collisions | [`ModelComposition`](../angee/compose/model_composition.py) |
| Concrete class source text | [`angee.compose.rendering`](../angee/compose/rendering.py) |
| Literal feature declarations and generated-file contributions | [`BuildIntegration`](../angee/compose/declarations.py), selected by installed autoconfig |
| REBAC Meta, additive grants and permission-schema projection | [`PermissionIntegration`](../addons/angee/base/build.py) |
| Source-local catalogue/tier projection and seeded-row retirement exemption | [`CatalogueIntegration`](../addons/angee/resources/build.py) |
| Web manifest and Tailwind source projection | [`WebIntegration`](../addons/angee/web/build.py) |
| Host URL/ASGI, middleware and external artifact ownership defaults | [`angee.autoconfig`](../angee/autoconfig.py) |
| Artifact assembly and explicit build/check/cleanup orchestration | [`Runtime`](../angee/compose/runtime.py) |
| Shared generated-artifact drift and guarded filesystem cleanup | [`GeneratedTree`](../angee/utils/artifacts.py) |
| Addon migration materialization | [`RuntimeMigrations`](../angee/compose/migrations.py) |
| Native package metadata and isolated compiler invocation | [`angee.hatch`](../angee/hatch.py) |
| Prebuilt command lifecycle | [`angee.management`](../angee/management/__init__.py), native [`angee` command](../angee/management/commands/angee.py) |
| Autodetected drop guards | [`RuntimeMigrations`](../angee/compose/migrations.py) refuses deleting a still-owned table at build; [`DropGuardAutodetector`](../angee/compose/migrations.py) refuses dropping a populated column or table in `makemigrations`, apart from rows the retiring addon declares seeded |
| Runtime import during Django app population | [`AngeeConfig.import_models()`](../angee/apps.py) |
| HTTP route aggregation | [`angee.urls`](../angee/urls.py) |
| WebSocket routes, HTTP sub-app mounts, mount lifespans | [`angee.asgi`](../angee/asgi.py) |
| GraphQL schema declarations and SDL output | `angee.graphql` (the folder addon under `addons/angee/graphql`) |
| MCP tool declarations and the `/mcp` StreamableHTTP mount | `angee.mcp` (the folder addon under `addons/angee/mcp`) |

If a fact belongs to one of these owners, update that owner or its docstring.
This document should point there, not repeat the contract.

## Settings Bootstrap

Django imports generated `runtime.settings`, which delegates to `ProjectContract`.
That owner finds the project root and loads project Python settings or a private YAML merge namespace,
applies the bounded django-yamlconf adapter, evaluates Angee defaults, makes
configured addon roots importable, and imports the build-selected native app
configurations and autoconfig modules. It reconstructs the native manifest values
emitted by the build and applies deployment-sensitive hooks through the same YAML
merge owner. Serving never resolves the app graph or discovers manifests. The
native metadata hook uses the declaration loader without populating Django; the
explicit build adapter calls `Composer`.
The compiler owns `COMPOSE_RUNTIME_DIR` and `COMPOSE_RUNTIME_MODULE`.
Only canonical `COMPOSE_RUNTIME_*` names configure generated output. Native
YAML provenance and reference expansion remain available. Ordered addon defaults
and derived values are evaluated at settings load, not frozen into output.

The baked runtime image supplies its folder-addon roots through the native
`YAMLCONF_CONFFILE` overlay, [docker/addon-settings.yaml](../docker/addon-settings.yaml).
Its additive declaration preserves project roots. An explicit deployment override
of `YAMLCONF_CONFFILE` replaces that image default; the replacement owns the full
overlay, including any baked addon roots the deployment needs.
Instance Python and web jobs use the same project mount beside the framework
sources, so generated relative paths and native pnpm workspace membership agree;
the [shared stack template](../templates/stacks/_shared/stack-body.yaml.jinja)
owns that layout.

Project settings may override framework defaults before composition. Settings
that are products of composition are reserved and are assigned by `Composer`; an
addon or project cannot redefine them with a conflicting value. The current
reserved set lives in `COMPOSER_OWNED_SETTINGS`.

`ANGEE_ADDON_DIRS` is interpreted during this phase only to make addon source
roots importable. It is not a second addon list: project roots are the configured
`INSTALLED_APPS` entries after the framework-owned core prefix.

## App Graph And Settings

`Composer` reads the supplied `INSTALLED_APPS`, resolves the full ordered app
set through `AppGraph`, and writes the same `AppConfig` objects back to
`INSTALLED_APPS`. Angee's host loader supplies its default app profile; installing
the composer alone adds no model toolkit, history, permissions, jobs or routes.
URL/ASGI and middleware defaults belong to the installed Angee host and can be
overridden by project settings. Direct Composer users make their import roots
available before resolving apps; Angee's host loader owns namespace-path refresh.

`AppGraph` delegates app creation to Django's `AppConfig.create()` and reads
required app paths from `AppDeclarationSource`. The generic default declares no
dependencies or migrations. Angee's host selects `ManifestDeclarations` through
`COMPOSE_APP_DECLARATIONS` before graph resolution; that adapter reads the same
native manifest already owned by `addon_manifest()`. That owner validates manifest
identity against `AppConfig.name`; capability readers share its parser result. AppConfig has no independently
configurable copy of these declarations. `AppGraph` owns Django identity aliases
and root/required-app annotations. Both it and the import-free
`resolve_manifest_roots()` projection delegate ordering and cycle rejection to
`hatch_angee.order_app_dependencies()`. This shared owner preserves
declared root precedence and visits dependency subtrees in lexical order;
manifest discovery omits plain Django apps and unselected available addons.

Django accepts `AppConfig` instances in `INSTALLED_APPS`, so app loading uses the
same config objects the composer already resolved instead of resolving strings a
second time.

### Addon Discovery

Catalogue discovery keeps hatch-angee's native manifests paired with their
`importlib.metadata.EntryPoint` or local `Path` origins in
[`available_addons()`](../addons/angee/platform/discovery.py). Installed lookup uses `find_spec()`,
which can import parent packages; only filesystem `discover()` belongs in the
import-free dependency bootstrap. Neither path populates Django's app registry.
When an available local or installed addon needs identity, `resolve_app_config()`
reuses the loaded config or delegates the actual app declaration to Django's
factory. Constructing a disabled config preserves its declared label without
enabling it or calling `ready()`. Unresolved available candidates and remote VCS
entries retain `label=""`, with the canonical name as their display fallback.
Remote entries do not resolve or import the named app: a matching local package
cannot establish the remote declaration's Django identity.

[`platform.Addon`](../addons/angee/platform/models.py) owns the persisted
catalogue projection. Its `depends_on` always records declared direct manifest
dependencies, including disabled and remote entries and the last known
declaration for removed rows. Its `depended_by` projection includes disabled
declarers from the available catalogue as well as loaded apps. The loaded
composition graph owns forced dependencies and disable admission.

## Autoconfig

After `INSTALLED_APPS` is resolved, `Composer` applies optional app settings
through `AutoConfig` in dependency order. Core and third-party apps are plain
`AppConfig` instances; folder addons additionally carry manifests.

Bare settings modules such as the source-addon test harness call
`AutoConfig.apply_installed(globals(), environment=False)` before Django loads models. They declare
the complete app list in contribution order and receive the same addon defaults
without generating a runtime. Django's app factory resolves their string entries;
the method also accepts the config instances already resolved by `Composer`.
Disabling environment input keeps shell service credentials and endpoints out of
isolated tests. Production composition keeps environment input enabled.

Any app may provide `<app>.autoconfig` with a `SETTINGS` mapping. The keys use
`django-yamlconf` syntax; `AutoConfig` owns the Angee rules around reserved
settings, app defaults, list/dict merging, and declared environment overlays.
`COMPOSE_ENV_PREFIXES` identifies setting names the host accepts directly from
the process environment; the generic default accepts none. Angee's host declares
`("ANGEE_",)`. Native `YAMLCONF_*` project definitions retain django-yamlconf's
own loading semantics. Apps still read `django.conf.settings`; process environment is
normalized during composition.
Implementation contributors add entries to the base's named setting. Native
`ImplClassField.check()` validates the bound mapping; the owning addon registers
a native system check for a rowless contract. No second registry declaration is needed.

Derived `settings(namespace)` hooks read only their supplied namespace.
`AutoConfig` gives enabled environment values precedence over project values,
so hooks inherit the same environment policy as declared settings without
reading `os.environ` themselves. Hook parsing still owns typed derived values.

`django_yamlconf` is in the framework-owned app prefix, so `ycexplain` and
`yclist` remain the provenance tools for composed settings.

Entry-level before/after ordering for list settings is intentionally not built
yet. Current order is addon dependency order plus yamlconf merge semantics. If a
real addon needs item-level ordering later, that belongs in `AutoConfig`, not in
`Composer`.

## Runtime Build And Import

The generated runtime is output, not source. It exists because Django concrete
model classes must live in importable modules; native migration history lives in the host.

`Runtime.from_django()` explicitly discovers the installed sources through
`ModelComposition`; it does not bind settings or write output. A `Runtime`
instance coordinates that composition with declared build integrations, migration
and filesystem owners. Each write or drift operation renders one source map and
passes it to `GeneratedTree`. Host dependency metadata belongs to the native Hatch metadata hook.
`GeneratedTree` validates every artifact against the effective root before reading
or writing it, including native model output. A configured root may itself be a
symlink; artifact paths must stay within its resolved destination.

Source selection uses ordinary abstract Django models and their own `runtime` /
`extends` declarations. The compiler, rather than methods on AngeeModel, owns
interpretation of those two markers:

- Own `runtime = True` without a target materializes a root model.
- Own `extends = "app.Model"` without own `runtime = True` contributes a same-row
  donor. The donor class itself participates in inheritance, including its fields,
  properties, methods and shared mixins.
- Both markers materialize a Django multi-table-inheritance child.

Donors and child sources are narrow abstract classes containing only their
contributed fields and behavior. Shared parent columns come from the concrete
parent. The renderer emits donors, then the source, then its concrete parent;
Django owns field construction, manager selection and Python method resolution.
Source Meta owns explicit model options, with the existing additive donor
constraint seam. See `ModelComposition` for validation and ordering rules,
including rejection of model-parent cycles and mutually importing generated app
modules.

An abstract capability may declare `requires_parent = "app.Model"` when its
methods require that materialized ancestor. The compiler checks every composed
source and donor using the capability against its declared `extends` chain,
including indirect parents. This requirement changes no Python bases or fields;
the parent remains the MTI persistence owner. The typing view consumes the same
validated contract. See [Bridge](../addons/angee/integrate/models.py) and
[`ModelComposition`](../angee/compose/model_composition.py).

Tracking and batch behavior stay with their owners. `HistoryMixin` uses a native
simple-history descriptor adapted for generated module labels and virtual fields;
every concrete descendant of a tracked source has its own history table,
including MTI children. Models declare their own managers and Meta;
the renderer never injects an Angee manager into a plain Django class.
`ModelHistory` refuses a base manager without its queryset capture capability.
Angee's model bases already declare the guarded managers; a plain tracked model
must declare an unfiltered history-capable manager explicitly. Resource
participants inherit the resources-owned
[`ResourceLoadMixin`](../addons/angee/resources/mixins.py) and delegate through
`super()`. Installed build integrations contribute literal feature attributes,
custom Meta options, generated text and explicit generated Python wiring through
[`declarations.py`](../angee/compose/declarations.py). They cannot replace native
fields, managers, methods or the compiler's structural declarations. Source/donor
ordering and collisions remain compiler-owned; REBAC and catalogue semantics
remain with their respective integrations. There is no executable-code callback
or resource lifecycle aggregator in the renderer.

Adding native history to an existing model emits only Django's schema changes:
the first tracked write after the upgrade is the row's first version. An addon
whose upgrade needs earlier snapshots declares that in its own runtime migration
(simple-history's `bulk_history_create` on the historical model); the composer
emits no checkpoint of its own.

The compiler emits package/model files, static web projections and effective
permission extensions. Native makemigrations materializes addon-owned upgrades.
Django owns the resulting model and migration graphs, `makemigrations`, execution,
rollback, routing and recording. GraphQL SDL and frontend codegen have separate
owners, so composer drift checks ignore their outputs. Explicit reset/cleanup
still clears those outputs while preserving migration subtrees.

### Addon-owned runtime migrations

An addon may declare ordered `[[migrations]]` entries in `addon.toml` when a
model transition cannot be represented losslessly by downstream
`makemigrations`:

```toml
[[migrations]]
name = "rename_legacy"
app_label = "resources"
module = "runtime_migrations.rename_legacy"
```

The source module is an ordinary, self-contained Django migration with a
`Migration` class plus a pure `applies(ProjectState) -> bool` guard. Keep it in
an addon package such as `runtime_migrations/`, never the conventional
`migrations/` package: Django would discover the latter as a migration of the
abstract source app before the composer can attach it to the downstream graph.

After an editable runtime build, `makemigrations` first asks Django to author
initial schema for selected composed apps that have no native migration history.
Previously authored app histories remain unchanged; separately selecting fresh
apps in successive commands follows the same native label-selection contract.
This bootstrap runs only for ordinary mutating authoring; read-only, merge,
empty and update modes retain their existing intent. It authors files without
applying or faking migrations. If later transition validation fails, these valid
native initials remain for review and the next authoring attempt.

The command then binds declarations into Django's migration graph before
evaluating guards or simulating model state. Existing histories always bind
transitions before native autodetection. New native filenames use the
declared `name`, so a migration can depend on a pending transition through ordinary
Django `dependencies`, for example `("resources", "copy_legacy")`. Manifest order
is not an execution dependency. Declare cross-app dependencies and `run_before`
explicitly; the composer does not infer data ordering from changes to schema.
Independent addon transitions targeting the same existing app history may instead
be joined with `makemigrations <app-label> --merge --name <unique-merge-name>`.
This explicit command validates each pending branch against its own prerequisite
state, stages the immutable source copies, and delegates the merge to Django.
Use an unused `--name` to preserve existing files and deterministic native naming;
reusing a name from native history is refused before staging.
Normal authoring still refuses split leaves; `--dry-run` and `--check` never stage
pending sources. Existing split host history must be merged before pending
transitions can attach to its leaf. The combined state and drop guard are checked
after native merge authoring; review and commit the branch and merge files together.

The target app's existing leaf is a prerequisite. The copied footer freezes the
resolved `dependencies` and `run_before`, including symbolic `__first__` and
`__latest__` references, and records the `<addon>:<name>` origin and source digest.
Existing names, including older numeric names, and copied source bodies remain
immutable. Repeated authoring does not create another node. Duplicate names/origins,
changed source or body digests, missing prerequisites, ambiguous existing leaves,
invalid states and cycles fail before source emission or database execution.
Pending split leaves require the explicit native merge authoring path above. Source
callbacks are copied with the migration so later addon changes do not replace
historical Python bodies.

Django's `MIGRATION_MODULES` owns the output package. Angee's host build defaults
each composed label to `<runtime_module>.<app_label>.migrations`, beside its generated models;
explicit mappings remain authoritative. The explicit build initializes an absent
default migration package so unlabelled native `makemigrations` discovers fresh apps;
existing package initializers and migration files remain untouched. Django authors
ordinary migrations and creates missing explicitly mapped packages when requested.
Read-only planning never creates a
missing package, and an explicit `None` disables migrations for that app. A
requested authored transition or history reset cannot target a disabled app.
Keep released migration files as durable deployment inputs, including histories
for retired models. An ignored generated directory alone is not a history
retention policy. The implementation and its exact contracts live in
[`RuntimeMigrations`](../angee/compose/migrations.py) and its
[mechanism tests](../tests/test_runtime_migrations.py).

A pure `applies(ProjectState)` guard checks whether a declaration is valid after
its declared prerequisites. `False` cannot prove that a data-only backfill ran.
An ambiguous pending declaration is refused rather than silently skipped. Fresh
installations need a valid native migration chain or a reviewed native squash
covering the complete chain. Keep the original migrations while partially
upgraded installations remain supported: Django selects the original chain when
only some replacement targets have applied. Merely listing a missing original
node in `replaces`, an initial-looking schema, or `--fake-initial` does not supply
that original upgrade path. A baseline must retain required data operations.

For a conversion followed by retirement, the retirement explicitly depends on
the conversion. Its native migration body verifies retained data before removing
fields or models. The shared physical-table ownership guard also refuses an
autodetected deletion while another model still owns that table. A validated
adoption may leave staging files available for a consumer cutover, but no build
executes database operations.

`reset_migration_history` is an exceptional, explicit upgrade cutoff. It is not
part of normal deployment or a way to correct an already published migration.
Add a new forward migration for a correction. Reset requires a fully applied,
model-clean graph; it replaces selected app histories and updates Django's
recorder in one transaction without recreating tables. External records remain
intact, and failure restores the original files and recorder state. Removing
history can permit a still-declared data transition to run again, so a reset
requires a deliberate support cutoff and retirement of superseded declarations.

Normal serving only imports prebuilt artifacts. The explicit `angee check`
compares generated output and reports pending declarations without writing files.
Use an editable `uv sync` to compose models, then native `makemigrations` to bind
authored transitions and generate remaining lossless changes. Review and commit
the resulting native history before `uv build`: packaging validates the sealed
history offline and never authors it. Provisioning checks for model drift and
uses native `migrate` for execution, database aliases, routing and recording.

`makemigrations` never drops a column or a table that still holds data.
The host’s explicit `makemigrations` authoring command loads the compiler’s
[`DropGuardAutodetector`](../angee/compose/migrations.py), which refuses before
any file is written:

- an autodetected `RemoveField` when a row differs from the field's empty value
  (NULL, or a non-null field's default);
- an autodetected `DeleteModel` when its table holds a row. An addon that removes
  a model may name it under `retired_models` in its `addon.toml` `[resources]`
  (for example `retired_models = ["messaging.ActivityType"]`): the rows its own
  resource files seeded then retire with the model. Resources' `CatalogueIntegration`
  implements the compiler's `retired_primary_keys` hook through its resource ledger
  (`ResourceQuerySet.retired_seed_keys`); other addons' seeded rows still count.
  Without an installed retirement integration every row remains protected. The ledger rows stay as `angee.resources` history; the loader skips a
  model it cannot resolve.

Both proofs run on every database that may receive the migration; without
`DATABASE_ROUTERS` that is `default` alone, as in Django's history consistency
check. A database that cannot be checked refuses the drop. The guard runs only
inside `makemigrations` and `migrate`, which need the database anyway;
`uv build` never probes it. Empty columns and tables drop automatically, so
toggling a layer in development stays free. Retire populated data with a
cutover: an addon runtime migration, declared by an addon that stays installed,
that preserves or retires the data and removes the field or model. Native
`makemigrations` materializes it first, so the reviewed removal is never
autodetected.

Follow the [migration policy](backend/guidelines.md#migration-history) when
authoring a data transition or resetting composed history.

[`angee provision`](../angee/management/commands/angee.py) prepares the database
from a prebuilt runtime in one native Django process. The operator's
`runtime-build` job completes first. Provision waits for the database, runs
`makemigrations --check --dry-run --noinput`, applies native migrations, refreshes
PostgreSQL statistics, syncs REBAC and runs native checks. It never builds
models or authors migration files. Pending transitions or model drift require
reviewed migration authoring before retrying. Each command retains its own
transactions, and a failure stops later steps.

Optional addon commands can follow provision through `--after-if-installed`;
the composer runs an explicitly named command only when Django's registry
contains it. The timeline reads source rows directly and requires no backfill.
An installed app targeted by a selected addon's transition retains its composed
label even after its last model retires, so Django can apply the cutover. Removing
the declaration, body or app then requires retiring every supported historical
consumer through an explicit upgrade cutoff; a completed local migration alone
does not authorize deleting the released upgrade path.

#### Foundation and provenance upgrade cutoff

Current source uses the real `angee.base` foundation addon and canonical
capability owners only. It contains no historical root import shims, alias-only
modules or completed transition declarations. The framework and companion
repositories' `feature/pre-refactor` snapshots are the upgrade bridge; an old
migration graph must cross the cutoff before those source snapshots are replaced.

From the controlling stack's native host environment:

1. Preserve the complete coordinated source snapshots, settings, database and
   generated runtime, including its migration files. Do not treat a source branch
   as a substitute for the database/runtime backup.
2. On the snapshots, apply pending migrations and finish representation changes
   through their existing owners. Canonicalize stored identities, option versions
   and other retained values before deleting the code that interprets their old
   representation. Browser-only appearance choices have a separate
   [device preservation step](frontend/appearance.md#host-and-user-precedence);
   a database reset cannot perform or prove it.
3. Run `manage.py reset_migration_history --database <alias>` only on a fully
   applied, model-clean composed graph. The native command verifies those gates,
   replaces composed history with fresh initials and records them without
   recreating application tables. Inventory the entire retained graph too:
   non-composed or inactive histories need their owning app's supported retirement
   or native reserialization, with operation/state equivalence verified. A graph
   shared by databases needs one coordinated cutoff before its files change.
4. Verify no retained migration imports a retired owner, then advance framework,
   addon pins and rendered host entrypoints together. The `angee.base` AppConfig
   retains Django's persisted `base` label; tables and content types keep their
   identities. New migrations serialize canonical field and callable owners.
5. Run native build/provision, migration/permission checks and schema/frontend
   generation against the resulting source. Verify both fresh installation and
   the populated upgrade; preserve evidence for the actual cutoff revision.

Completed addon `[[migrations]]` entries and their source bodies retire after
application and history consolidation. The generic compiler, materializer, drop
and drift guards and reset command remain for future write → apply → reset →
delete transitions. Do not restore old API paths to keep a pre-cutoff graph alive;
use the snapshots and complete its cutoff first.

Provenance has one physical [addon owner](../addons/angee/provenance/README.md),
and base models opt into its mixins explicitly. Current callers use those owners
directly; no second provenance or foundation implementation remains at an old path.

### Composed addon dependencies

Each addon's `addon.toml` owns its third-party requirements. The host opts into
Angee's native Hatch metadata and build hooks. The metadata hook uses the existing
manifest closure and hatch-angee dependency compiler to combine selected addon
requirements with the host's declared requirements into native `Requires-Dist`.
There is no generated dependency group, source pyproject writer or inner installer.
Core requirements remain in the core wheel; unavailable or unselected addons
contribute nothing.

`uv sync` builds the editable host with the same hook used by `uv build`. The hook
stages the authored config and addon source closure in a portable layout before
compiling. Raw environment references remain references; resolved secrets and
`.env` are never serialized. Native history is version controlled beside generated models under
`runtime/<label>/migrations/` and bundled with the project-specific wheel. A normal wheel
build validates history and model drift without generating migrations or using a
DB. The [native history workflow](#addon-owned-runtime-migrations) explains authoring.

### Runtime cleanup

The host’s `AngeeConfig` only imports prebuilt concrete models. Explicit
`BuildComposeConfig` uses the same compiler after native source model hooks and
imports concrete models before `ready()`. Missing output never triggers serving
repair.

Explicit build reconciles only its owned files. Declared external producer
outputs such as GraphQL SDL, codegen and web bundles survive a model rebuild.
Owned stale files and empty directories are pruned deterministically after the
configured root and marker have been checked. Shared `GeneratedTree` implements
neutral IO; Runtime supplies its own marker and history-preserving ownership.

`angee clean` removes generated producer outputs under that guarded runtime,
while retaining native migration history and the marker when retained content
remains. It does not render source declarations. Ordinary command setup remains
import-only, so cleanup and read-only checks cannot silently recreate output.
Native project `MIGRATION_MODULES` overrides remain authoritative; generation
never creates competing history under the default runtime path for custom or
disabled mappings. History reset is a separate guarded explicit command.

## Addon Declarations

An Angee addon is a Django app marked by a co-located `addon.toml`. No Angee base
config is required. `apps.py` is optional; use it for native config customization
or lifecycle hooks such as `ready()`. The declarative addon contract stays in the
manifest, and each capability reads the section it owns.

Routes are conventional: `angee.urls` looks for `urls.py`, and `angee.asgi`
looks for `asgi.py`, but only on apps that are Angee addons (they carry a manifest)
per `angee.addons.is_angee_addon()`. That keeps third-party apps that happen to ship
route modules from leaking into the composed root router.

Each capability owner reads the relevant native manifest section and loads its
implementation when the required Django phase is ready. GraphQL owns schema
objects, MCP owns registrar callables, resources owns resource declarations, and
migration composition owns migration entries. Missing optional conventional
exports mean no contribution; a broken module or missing explicit reference is an
error. Discovery does not inspect Python ASTs to predict runtime exports.

`WebRuntime` reads `[web]` declarations, falling back to `web/package.json` for the
conventional package. It renders `runtime/web/manifest.json` and Tailwind sources
without importing GraphQL schemas. Each package entry lists, as `dependsOn`, the
web packages its addon depends on transitively (`angee.addons.addon_ancestors`),
and the deployment's `ANGEE_UI` setting (`menus`, `containers`, `shell`) rides
along as `deployment`. The composer refuses unknown keys and a malformed
`shell`; `@angee/app` checks the values against the composed menus, routes,
icons and themes at boot. The frontend
codegen owner consumes that manifest and SDL to produce `runtime/gql/` and
`runtime/web/app.ts`, attaching each manifest's ancestry as web ids and appending
the deployment as the last layer.
The generated web module exposes `loadComposedSchemas()` for parallel fetches of
the emitted schema metadata JSON assets. The rendered host passes this loader to
`@angee/app`'s `bootApp`; it awaits metadata before synchronous `createApp`
composition. The app Vite config preloads those assets in built HTML.

The durable boundary is: declare addon facts in `addon.toml`, derive integration
from the unchanged hatch-angee manifest, and keep implementation with its owner.
Shared import utilities in `angee.addons` handle references and optional modules;
they do not maintain a second contract or infer capability values.

Resource relocation with renamed workflow keys requires an explicit data
migration: renamed workflow keys cannot adopt old definitions through
[`AngeeResource._adopt_for_row`](../addons/angee/resources/loader.py) with
`adopt = "key"`. Removing a resource manifest declaration does not retire its rows or
ledgers; [`WorkflowManager.install_definition`](../addons/angee/workflows/managers.py)
installs the supplied whole document, with no relocation prune/alias path.

## Serving

`angee.urls` and `angee.asgi` are stable framework entrypoints. They are not
generated into the runtime directory.

`angee.urls` walks the composed app registry in dependency order and aggregates
URL patterns from opted-in addon `urls.py` modules.

The shared [host bootstrap](../angee/project.py) respects an already configured
Django application or an explicitly selected settings module. Importing an
optional capability does not rediscover a surrounding Angee project.

`angee.asgi` bootstraps the composed settings module for direct ASGI imports,
builds Django's ASGI application, and wraps it only when installed addons
contribute WebSocket patterns or HTTP sub-app mounts through `asgi.py`. The exact
`websocket_urlpatterns` and `http_mounts` contracts live in the `angee.asgi`
docstrings. The MCP addon is the current HTTP-mount example: `angee.mcp.asgi`
contributes the FastMCP StreamableHTTP app, and `angee.asgi` enters mounted
lifespans from the server's ASGI lifespan.

Other lifecycle commands follow the same shape: enumerate Django's app registry
and consume only the declarations or conventional route modules they own. The
composer does not keep a parallel addon registry.

## Example Project Shape

A minimal YAML project declares roots and project-owned paths:

```yaml
SECRET_KEY: notes-example-dev-key

INSTALLED_APPS:
  - angee.integrate
  - angee.operator
  - example.notes

ANGEE_ADDON_DIRS:
  - "{BASE_DIR}/addons"
COMPOSE_RUNTIME_DIR: "{BASE_DIR}/runtime"
ANGEE_DATA_DIR: "{BASE_DIR}/../../.angee/data"
```

The installed Angee host supplies URL and ASGI defaults, which project settings
may override. The host build factory defaults emitted labels to `<runtime_module>.<label>.migrations`
and preserves explicit mappings, including `None`. The artifact records the effective
native mapping; changing it after packaging requires rebuilding.

## Invariants

- `INSTALLED_APPS` is the project root addon contract.
- There is one resolved Django app set and one boot path.
- Project settings are composed before app loading; phase 2 binds derived migration and permission paths.
- Settings composition does not import source models.
- Rendering produces artifacts; explicit lifecycle operations bind derived integration paths.
- Normal startup imports prebuilt artifacts only; it never scans declarations or writes.
- Explicit composer drift checks cover runtime sources and addon migration
  declarations; GraphQL SDL and frontend codegen checks
  belong to their respective owners.
- Apps read `django.conf.settings`; process environment is normalized during
  settings composition.
- `addon.toml` owns addon declarations; native AppConfig owns Django identity and lifecycle.
- Capability conventions are defaults, with explicit manifest declarations taking precedence.
- Generated `runtime/` is output; edit addon source, not emitted files.
- Base workflow graphs are complete documents in declared resource files, as in the
  [notes workflow](../examples/addons/example/notes/resources/demo/100_workflows.workflow.yaml).
  [`WorkflowDefinitionResource`](../addons/angee/workflows/resources.py) delegates
  identity, draft validation and publication to `WorkflowManager.install_definition`.
  Addons may contribute fragments only through declared definition points and the
  `workflows.workflowextension` install kind; the resource loader composes those
  fragments for publication without modifying the base draft. Publication freezes
  the normalized child contracts in each version. Edit the source declaration and
  republish affected parents when a child contract changes.
- Runtime cleanup may delete only the configured generated runtime directory,
  only after verifying Angee's generated sentinel, and must preserve migrations.
  `reset_migration_history` owns the separately guarded history replacement.
