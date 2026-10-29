# The Composer

The composer is Angee's Django composition layer. It turns a project's normal
Django settings contract and installed addon apps into one runnable Django
project: final settings, a single ordered app registry, generated concrete ORM
models and a shared generated-file lifecycle. Angee's host and installed
capabilities supply URL/ASGI defaults and their build integrations.

A project declares root apps with `INSTALLED_APPS`. Each addon is a Python
package whose `pyproject.toml` declares a distribution and one `angee.addons`
entry point. Native requirements own installation and addon activation edges;
Django owns AppConfig selection, labels, paths, model registration and `ready()`.
Each capability reads only its own `[tool.angee.<capability>]` table. Executable
behavior remains in Python; conventional modules supply defaults.

This page maps the flow and ownership boundaries. Current API details, default
lists, validation rules, and exact declaration shapes live in the owning modules,
classes, and docstrings linked below.

## Flow

A normal project declares `INSTALLED_APPS` and overrides in `settings.yaml`.
Addon defaults and derived settings remain with their addons; native yamlconf
merges the ordered contributions. Deployment settings and secrets stay late-bound.

1. **Dependency installation** — native `uv sync` installs distributions and
   editable development packages. It does not compose the project.
2. **Explicit prebuild** — the operator's `runtime-build` job calls
   `python -m angee.build --root . --output runtime` in a fresh process after
   dependency sync. Production adds `--check-history` for offline tracked-history
   validation. Source `import_models()` hooks run once; the current compiler emits
   and imports concrete modules before `ready()`. Builds never provision a DB.
3. **Serving startup** — Django imports generated `runtime.settings`. The native
   `AngeeConfig` validates the built ordered app identities and imports prebuilt
   concrete models during phase 2. Missing output requires an explicit rebuild.
4. **Native history authoring** — after a development build, `makemigrations`
   binds sorted addon upgrade sources and writes tracked native history beside
   generated models. Review and commit `runtime/<label>/migrations/`.
5. **Provisioning** — a fresh prebuilt Django process validates history drift,
   migrates, syncs policy and runs declared addon commands. The operator then
   restarts services. `uv run --no-sync python manage.py angee check` checks
   artifact drift without writing; serving never repairs generated output.

## Owner Map

| Concern | Owner |
|---|---|
| Project-root discovery and project settings/bootstrap environment | [`ProjectContract`](../core/angee/conf.py), called by generated `runtime.settings` |
| Bounded project YAML loading and accepted source provenance | [`ProjectContract`](../core/angee/conf.py) |
| Native YAML settings merge and source inspection | [`angee.utils.yamlconf`](../core/angee/utils/yamlconf.py) |
| Overridable framework defaults and ordered always-on core apps | [`angee.defaults`](../core/angee/defaults.py) |
| Reserved composed settings and final settings mutation | [`AutoConfig`](../core/angee/utils/autoconfig.py) owns reserved names; [`Composer`](../core/angee/compose/composer.py) assigns their values |
| Django app discovery, identity aliases and root annotations | [`AppGraph`](../core/angee/compose/appgraph.py) |
| Dependency ordering and cycle rejection for both discovery paths | `hatch_angee.order_app_dependencies` |
| App settings fragments and declared host env overlays | [`AutoConfig`](../core/angee/utils/autoconfig.py) |
| Host package adapter | [`PackageDeclarations`](../core/angee/build.py) implements [`AppDeclarationSource`](../core/angee/compose/declarations.py), selected by `COMPOSE_APP_DECLARATIONS` before app resolution |
| Addon declarations and parsing | Native entry points, installed distribution metadata and package TOML; [`angee.addons`](../core/angee/addons.py) projects these facts |
| Abstract-source discovery, donor order, parent relationships and collisions | [`ModelComposition`](../core/angee/compose/model_composition.py) |
| Concrete class source text | [`angee.compose.rendering`](../core/angee/compose/rendering.py) |
| Literal feature declarations and generated-file contributions | [`BuildIntegration`](../core/angee/compose/declarations.py), selected by installed autoconfig |
| REBAC Meta, additive grants and permission-schema projection | [`PermissionIntegration`](../addons/angee/base/build.py) |
| Source-local catalogue/tier projection and seeded-row retirement exemption | [`CatalogueIntegration`](../addons/angee/resources/build.py) |
| Web manifest and Tailwind source projection | [`WebIntegration`](../addons/angee/web/build.py) |
| Host URL/ASGI, middleware and external artifact ownership defaults | [`angee.autoconfig`](../core/angee/autoconfig.py) |
| Artifact assembly and explicit build/check/cleanup orchestration | [`Runtime`](../core/angee/compose/runtime.py) |
| Shared generated-artifact drift and guarded filesystem cleanup | [`GeneratedTree`](../core/angee/utils/artifacts.py) |
| Addon migration materialization | [`RuntimeMigrations`](../core/angee/compose/migrations.py) |
| Explicit isolated compiler invocation | [`angee.build`](../core/angee/build.py) |
| Prebuilt command lifecycle | [`angee.management`](../core/angee/management/__init__.py), native [`angee` command](../core/angee/management/commands/angee.py) |
| Autodetected drop guards | [`RuntimeMigrations`](../core/angee/compose/migrations.py) refuses deleting a still-owned table at build; [`DropGuardAutodetector`](../core/angee/compose/migrations.py) refuses dropping a populated column or table in `makemigrations`, apart from rows the retiring addon declares seeded |
| Runtime import during Django app population | [`AngeeConfig.import_models()`](../core/angee/apps.py) |
| HTTP route aggregation | [`angee.urls`](../core/angee/urls.py) |
| WebSocket routes, HTTP sub-app mounts, mount lifespans | [`angee.asgi`](../core/angee/asgi.py) |
| GraphQL schema declarations and SDL output | `angee.graphql` (the folder addon under `addons/angee/graphql`) |
| MCP tool declarations and the `/mcp` StreamableHTTP mount | `angee.mcp` (the folder addon under `addons/angee/mcp`) |

If a fact belongs to one of these owners, update that owner or its docstring.
This document should point there, not repeat the contract.

## Settings Bootstrap

Django imports generated `runtime.settings`, which delegates to `ProjectContract`.
That owner finds the project root and loads project Python settings or a private YAML merge namespace,
applies the bounded django-yamlconf adapter, evaluates Angee defaults, makes
configured addon roots importable, and imports the build-selected native app
configurations and autoconfig modules. It applies deployment-sensitive hooks through the same YAML merge owner. Package
capability facts come from their installed package data. Serving never resolves
the dependency graph; the explicit build adapter calls `Composer`.
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
required app paths from `AppDeclarationSource`. The host's `PackageDeclarations`
adapter reads installed `Requires-Dist` through `packaging.Requirement` and the
entry-point identity index, plus foreign `requires_apps` on AppConfig. Only
requirements whose markers are true with `extra=""` activate addon dependencies.
Explicit root config choices survive resolution. Platform previews and web
ancestry project the same dependency facts through `resolve_package_roots()`;
ordering and cycle rejection remain with `hatch_angee.order_app_dependencies()`.

Django accepts `AppConfig` instances in `INSTALLED_APPS`, so app loading uses the
same config objects the composer already resolved instead of resolving strings a
second time.

### Addon Discovery

Catalogue discovery pairs installed package facts with their native entry points
in [`available_addons()`](../addons/angee/platform/discovery.py). Wheels read the
shipped `pyproject.toml` through RECORD; editable installs use PEP 610's project
URL. Neither path imports a disabled app. Only loaded AppConfigs establish Django
labels; disabled and remote rows use an empty label and display their canonical
app name. Uninstalled projects belong to the VCS catalogue. That owner indexes
all discovered pyprojects before projecting requirements, so remote addon edges
use declared identities as well as installed identities.

[`platform.Addon`](../addons/angee/platform/models.py) owns the persisted
catalogue projection. Its `depends_on` always records declared direct addon requirement
dependencies, including disabled and remote entries and the last known
declaration for removed rows. Its `depended_by` projection includes disabled
declarers from the available catalogue as well as loaded apps. The loaded
composition graph owns forced dependencies and disable admission.

## Autoconfig

After `INSTALLED_APPS` is resolved, `Composer` applies optional app settings
through `AutoConfig` in dependency order. Core and third-party apps are plain
`AppConfig` instances; addons additionally expose installed package metadata.

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
passes it to `GeneratedTree`. Native distribution metadata owns dependencies; the explicit host worker owns compilation.
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
[`ModelComposition`](../core/angee/compose/model_composition.py).

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
[`declarations.py`](../core/angee/compose/declarations.py). They cannot replace native
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

An addon supplies an ordinary self-contained Django migration at
`compose_migrations/<app_label>/<name>.py` when native `makemigrations` cannot
represent a transition losslessly. The source declares `Migration` and a pure
`applies(ProjectState) -> bool` guard. Discovery sorts source paths and retains
the target label and filename as the migration's identity. Keep these sources
outside conventional `migrations/`, which Django discovers on the abstract app.

After an editable runtime build, `makemigrations` first asks Django to author
initial schema for selected composed apps that have no native migration history.
Previously authored app histories remain unchanged; separately selecting fresh
apps in successive commands follows the same native label-selection contract.
If Django expands an initial migration to changes in an existing app, bootstrap
refuses before writing: author that app's transitions and changes first.
This bootstrap runs only for ordinary mutating authoring; read-only, merge,
empty and update modes retain their existing intent. It authors files without
applying or faking migrations. If later transition validation fails, these valid
native initials remain for review and the next authoring attempt.

The command then binds declarations into Django's migration graph before
evaluating guards or simulating model state. Existing histories always bind
transitions before native autodetection. New native filenames use the
declared `name`, so a migration can depend on a pending transition through ordinary
Django `dependencies`, for example `("resources", "copy_legacy")`. Sorted source order
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
[`RuntimeMigrations`](../core/angee/compose/migrations.py) and its
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
[`DropGuardAutodetector`](../core/angee/compose/migrations.py), which refuses before
any file is written:

- an autodetected `RemoveField` when a row differs from the field's empty value
  (NULL, or a non-null field's default);
- an autodetected `DeleteModel` when its table holds a row. An addon that removes
  a model may name it under `retired_models` in its `pyproject.toml` `[tool.angee.resources]`
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

[`angee provision`](../core/angee/management/commands/angee.py) prepares the database
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

Completed addon `compose_migrations/` source files retire after
application and history consolidation. The generic compiler, materializer, drop
and drift guards and reset command remain for future write → apply → reset →
delete transitions. Do not restore old API paths to keep a pre-cutoff graph alive;
use the snapshots and complete its cutoff first.

Provenance has one physical [addon owner](../addons/angee/provenance/README.md),
and base models opt into its mixins explicitly. Current callers use those owners
directly; no second provenance or foundation implementation remains at an old path.

### Composed addon dependencies

Each addon owns its static `[project].dependencies` and one `angee.addons`
entry point. `uv add <distribution>` installs the declared closure. The framework
root is the `angee` meta-distribution; `core/pyproject.toml` owns `django-angee`.
There is no generated dependency group or host metadata hook. Development source
overrides use native uv sources for every selected member. Package installation
and explicit runtime generation remain separate operator jobs.

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

An Angee addon is a Django app advertised by an installed `angee.addons` entry
point. `apps.py` is optional and owns native identity customization, lifecycle,
and `requires_apps` for foreign Django app edges. Native `[project]` metadata
owns distribution identity, presentation and requirements; `[tool.angee]` holds
catalogue category and capability tables, read by their owning addons.

Conventional routes (`urls.py`, `asgi.py`), GraphQL (`schema.py`) and MCP
(`mcp_tools.py`) contribute only for declared addons. Non-default references live
under `[tool.angee.graphql] schema`, `[tool.angee.base] permissions`, and
`[tool.angee.mcp] tools`. Missing conventional exports contribute nothing;
broken modules and missing explicit references fail clearly.

Resources read `[tool.angee.resources]`. Authored paths are relative to
`resources/`, including local and cross-addon `depends_on` addresses. The resource
owner retains canonical addon-relative entry keys and persisted `source_path`
values with the `resources/` prefix, preserving existing ledger identities.
Resource-entry dependencies remain separate from package requirements.

`WebRuntime` reads `web/package.json`: native `name` owns npm identity, and
`angee.codegen` owns external SDL/document declarations. It emits the web
transport manifest and Tailwind sources without importing schemas. Shared host
requirement facts supply each package's transitive web ancestry. The deployment's
`ANGEE_UI` setting contributes `menus`, `containers` and `shell`; Python validates
JSON transport and `@angee/app` validates the composed values. Codegen consumes
that manifest and SDL to emit `runtime/gql/` and `runtime/web/app.ts`. The host
awaits `loadComposedSchemas()` before synchronous `createApp` composition.

Shared import utilities in `angee.addons` resolve references and optional
modules; each capability validates and interprets its own table.

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

The shared [host bootstrap](../core/angee/project.py) respects an already configured
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
ANGEE_DATA_DIR: "{BASE_DIR}/../../.core/angee/data"
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
- Native package metadata owns addon identity and requirements; each capability owns its tool table, and AppConfig owns Django lifecycle.
- Capability conventions are defaults, with explicit tool-table declarations taking precedence.
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
