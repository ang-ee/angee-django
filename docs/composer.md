# The Composer

The composer is Angee's Django composition layer. It turns a project's normal
Django settings contract and installed addon apps into one runnable Django
project: final settings, a single ordered app registry, generated concrete ORM
models, stable URL/ASGI entrypoints, and lifecycle inputs for GraphQL,
resources, permissions, MCP, and other addons.

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

Composition has three phases.

1. **Settings bootstrap** - Django imports `angee.compose.settings`. The settings
   module finds the project root, loads project settings, applies Angee defaults,
   resolves the addon graph, and mutates the namespace Django is importing.
2. **App loading** - Django populates the resolved `INSTALLED_APPS`.
   In population phase 2, `ComposeConfig.import_models()` discovers abstract
   sources, binds migration modules, repairs generated sources, binds effective
   permission paths, and imports the concrete models under the source app labels.
   Native `ready()` hooks run afterward, once the model registry is complete.
3. **Serving and lifecycle commands** - stable framework entrypoints such as
   `angee.urls`, `angee.asgi`, `schema`, `resources`, and `rebac sync` read the
   finished Django app registry. Each lifecycle consumes only the app
   declarations or conventional modules it owns.

Settings composition never imports source models or permission runtime code.
The phase-2 hook binds derived migration and permission paths explicitly; source
rendering does not choose project settings. Normal startup repairs files without
pruning. Explicit build and clean retain the guarded cleanup policy described
below.

## Owner Map

| Concern | Owner |
|---|---|
| Project-root discovery and project settings/bootstrap environment | [`ProjectContract`](../angee/compose/project.py), called by [`angee.compose.settings`](../angee/compose/settings.py) |
| Bounded django-yamlconf loading and provenance | [`angee.compose.yamlconf`](../angee/compose/yamlconf.py) |
| Overridable framework defaults and ordered always-on core apps | [`angee.compose.defaults`](../angee/compose/defaults.py) |
| Reserved composed settings and final settings mutation | [`Composer`](../angee/compose/composer.py) |
| Root/dependency graph, app aliases, root annotations | [`AppGraph`](../angee/compose/appgraph.py) |
| Addon settings fragments and declared `ANGEE_*` env overlays | [`AutoConfig`](../angee/compose/autoconfig.py) |
| Addon declarations and parsing | `addon.toml` and hatch-angee's native `AddonManifest`; [`angee.addons`](../angee/addons.py) binds the result to a native config |
| Abstract-source discovery, donor order, parent relationships and collisions | [`ModelComposition`](../angee/compose/model_composition.py) |
| Concrete class source text | [`angee.compose.rendering`](../angee/compose/rendering.py) |
| Artifact assembly and explicit build/check/cleanup orchestration | [`Runtime`](../angee/compose/runtime.py) |
| Atomic writes, drift and guarded filesystem cleanup | [`GeneratedTree`](../angee/fs.py) |
| Addon migration materialization and dependency projection | [`RuntimeMigrations`](../angee/compose/migrations.py), [`AddonDependencyGroup`](../angee/compose/dependencies.py) |
| Runtime import during Django app population | [`ComposeConfig.import_models()`](../angee/compose/apps.py) |
| HTTP route aggregation | [`angee.urls`](../angee/urls.py) |
| WebSocket routes, HTTP sub-app mounts, mount lifespans | [`angee.asgi`](../angee/asgi.py) |
| GraphQL schema declarations and SDL output | `angee.graphql` (the folder addon under `addons/angee/graphql`) |
| MCP tool declarations and the `/mcp` StreamableHTTP mount | `angee.mcp` (the folder addon under `addons/angee/mcp`) |

If a fact belongs to one of these owners, update that owner or its docstring.
This document should point there, not repeat the contract.

## Settings Bootstrap

Django imports `angee.compose.settings`, which delegates to `ProjectContract`.
That owner finds the project root, loads or synthesizes its settings module,
applies the bounded django-yamlconf adapter, evaluates Angee defaults, makes
configured addon roots importable, and calls `Composer`. The same loader serves
the import-free dependency bootstrap without constructing Django app configs.

Project settings may override framework defaults before composition. Settings
that are products of composition are reserved and are assigned by `Composer`; an
addon or project cannot redefine them with a conflicting value. The current
reserved set lives in `COMPOSER_OWNED_SETTINGS`.

`ANGEE_ADDON_DIRS` is interpreted during this phase only to make addon source
roots importable. It is not a second addon list: project roots are the configured
`INSTALLED_APPS` entries after the framework-owned core prefix.

## App Graph And Settings

`Composer` reads the core-prefixed `INSTALLED_APPS`, resolves the full ordered app
set through `AppGraph`, writes the resolved `AppConfig` objects back to
`INSTALLED_APPS`, and sets the stable framework entrypoints.

`AppGraph` delegates app creation to Django's `AppConfig.create()` and expands
addon dependencies from the native manifest. `addon_manifest()` validates that
manifest identity agrees with `AppConfig.name`; capability readers share that
upstream parser result during the composition. AppConfig has no independently
configurable copy of these declarations. Graph annotations record derived root
and required-app facts. Aliasing, duplicate handling and cycle validation belong
to `AppGraph`.

Django accepts `AppConfig` instances in `INSTALLED_APPS`, so app loading uses the
same config objects the composer already resolved instead of resolving strings a
second time.

## Autoconfig

After `INSTALLED_APPS` is resolved, `Composer` applies optional app settings
through `AutoConfig` in dependency order. Core and third-party apps are plain
`AppConfig` instances; folder addons additionally carry manifests.

Any app may provide `<app>.autoconfig` with a `SETTINGS` mapping. The keys use
`django-yamlconf` syntax; `AutoConfig` owns the Angee rules around reserved
settings, app defaults, list/dict merging, and declared `ANGEE_*` environment
overlays. Apps still read `django.conf.settings`; process environment is
normalized during composition.

`django_yamlconf` is in the framework-owned app prefix, so `ycexplain` and
`yclist` remain the provenance tools for composed settings.

Entry-level before/after ordering for list settings is intentionally not built
yet. Current order is addon dependency order plus yamlconf merge semantics. If a
real addon needs item-level ordering later, that belongs in `AutoConfig`, not in
`Composer`.

## Runtime Build And Import

The generated runtime is output, not source. It exists because Django concrete
model classes must live in importable modules with migration packages.

`Runtime.from_django()` explicitly discovers the installed sources through
`ModelComposition`; it does not bind settings or write output. A `Runtime`
instance coordinates that composition with the existing web, permissions,
dependency, migration and filesystem owners. Each write or drift operation renders
one source map and passes it to `GeneratedTree`.

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

Tracking and batch behavior stay with their owners. `HistoryMixin` uses a native
simple-history descriptor adapted for generated module labels and virtual fields;
a concrete parent's tracking alone does not opt its MTI child into another
history table. Revision sources use django-reversion registration on completed
concrete classes. Resource participants inherit the resources-owned
[`ResourceLoadMixin`](../addons/angee/resources/mixins.py) and delegate through
`super()`. The renderer has no generic decorator/attribute language or resource
hook aggregator.

The composer emits package/model files, static web projections and effective
permission extensions, and explicitly materializes addon-owned migrations.
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
name = "relationship_anchor"
app_label = "parties"
module = "runtime_migrations.relationship_anchor"
```

The source module is an ordinary, self-contained Django migration with a
`Migration` class plus a pure `applies(ProjectState) -> bool` guard. Keep it in
an addon package such as `runtime_migrations/`, never the conventional
`migrations/` package: Django would discover the latter as a migration of the
abstract source app before the composer can attach it to the downstream graph.

Explicit `angee build` evaluates declarations in addon and manifest order. For
each applicable origin it copies the complete source module to
`runtime/<app_label>/migrations/`, gives it the next numeric name, and attaches
it to the target app's single current leaf. The footer records the stable
`<addon>:<name>` origin and source digest. Existing origins are immutable and
idempotent; source edits, copied-body edits, duplicate origins, split leaves,
and invalid graphs fail before any planned file is written. A dependency on
`(<app_label>, "__latest__")` is resolved to a concrete current leaf when copied.

Normal app boot and `emit_if_stale()` never materialize migrations.
`angee build --check` validates existing history and reports applicable pending
origins without writing. After a successful build, normal `makemigrations` may
generate any remaining lossless changes and Django handles the rest of the
migration lifecycle.

### Composed addon dependencies

Each addon's `addon.toml` owns its third-party `dependencies`. After runtime
emission, explicit `angee build` passes the manifests for the resolved Django app
graph to hatch-angee and writes their exact sorted union into the host
`pyproject.toml` as the generated `[dependency-groups].addons` key. The framework
core has no manifests; its dependencies live in the wheel's package metadata.
Other plain Django apps and addons that are available but not composed contribute
nothing.

The hatch-angee writer preserves unrelated TOML comments and formatting, writes
atomically, and leaves an identical file untouched. A bare host with no
`pyproject.toml` is an explicit build-time skip so framework unit tests and minimal
Django projects can still emit a runtime. A fresh generated host must bootstrap
before Django loads addon apps: run `uv sync`, then
`uv run python -m angee.compose.bootstrap` to resolve the root addons'
manifest-only dependency closure and materialize the addon group, `uv sync`
again to install that group, and only then `uv run manage.py angee build`. The
standalone bootstrap reuses `ProjectContract`'s bounded django-yamlconf loader
and the same `AddonDependencyGroup` compile/write core as the normal build path;
it never creates an `AppConfig` or touches Django's app registry.

Import-free bootstrap expects canonical addon module names in project roots and
manifest dependencies. An arbitrary external AppConfig class path cannot identify
its addon without importing Python; such roots require their dependencies to be
installed before normal Django composition.

`ComposeConfig.import_models()` is the Django app-loading hook. In population
phase 2 it discovers sources, calls `configure_migration_modules()`, repairs output
with `emit_if_stale()`, and imports generated models. Final transition metadata is
validated against those concrete classes.

- `emit_if_stale()` is write-only and idempotent. It repairs missing or stale
  generated sources file by file before import, and it never resets, cleans, or
  materializes addon migrations.
- When explicit build needs a reset, it verifies the generated sentinel and
  configured root before clearing output. This removes orphaned labels and other
  generated files, including SDL/codegen, while preserving every migration subtree.
- `angee clean` uses the same guarded cleanup without discovering or rendering
  sources in its handler. Django setup still precedes management-command dispatch,
  so normal boot repair also precedes `angee clean` and `angee build --check`.

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
without importing GraphQL schemas. The frontend codegen owner consumes that
manifest and SDL to produce `runtime/gql/` and `runtime/web/app.ts`.

The durable boundary is: declare addon facts in `addon.toml`, derive integration
from the unchanged hatch-angee manifest, and keep implementation with its owner.
Shared import utilities in `angee.addons` handle references and optional modules;
they do not maintain a second contract or infer capability values.

## Serving

`angee.urls` and `angee.asgi` are stable framework entrypoints. They are not
generated into the runtime directory.

`angee.urls` walks the composed app registry in dependency order and aggregates
URL patterns from opted-in addon `urls.py` modules.

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
ANGEE_RUNTIME_DIR: "{BASE_DIR}/runtime"
ANGEE_DATA_DIR: "{BASE_DIR}/../../.angee/data"
```

The project does not declare the framework URL or ASGI entrypoints; those are
composer-owned. The phase-2 integration binds `MIGRATION_MODULES` for emitted
labels and preserves unrelated project entries. Conflicting paths, including an
explicit `None` disabling migrations for an emitted label, fail clearly.

## Invariants

- `INSTALLED_APPS` is the project root addon contract.
- There is one resolved Django app set and one boot path.
- Project settings are composed before app loading; phase 2 binds derived migration and permission paths.
- Settings composition does not import source models.
- Rendering produces artifacts; explicit lifecycle operations bind derived integration paths.
- Normal startup heals the runtime in place but never resets or prunes it.
- Composer drift checks cover composer-owned runtime sources only.
- Apps read `django.conf.settings`; process environment is normalized during
  settings composition.
- `addon.toml` owns addon declarations; native AppConfig owns Django identity and lifecycle.
- Capability conventions are defaults, with explicit manifest declarations taking precedence.
- Generated `runtime/` is output; edit addon source, not emitted files.
- Runtime cleanup may delete only the configured generated runtime directory,
  only after verifying Angee's generated sentinel, and must preserve migrations.
