# The Composer

The composer is Angee's Django composition layer. It turns a project's normal
Django settings contract and installed addon apps into one runnable Django
project: final settings, a single ordered app registry, generated concrete ORM
models, stable URL/ASGI entrypoints, and lifecycle inputs for GraphQL,
resources, permissions, MCP, and other addons.

The important constraint is that Angee stays Django-shaped. A project declares
root addons with `INSTALLED_APPS`; addons are plain Django apps with extra
`AppConfig` declarations for the lifecycle seams they participate in; HTTP and
WebSocket routes use Django-style conventional modules. Settings are ready
before Django app loading, and generated models are imported during Django app
loading.

This page maps the flow and ownership boundaries. Current API details, default
lists, validation rules, and exact declaration shapes live in the owning modules,
classes, and docstrings linked below.

## Flow

Composition has three phases.

1. **Settings bootstrap** - Django imports `angee.compose.settings`. The settings
   module finds the project root, loads project settings, applies Angee defaults,
   resolves the addon graph, and mutates the namespace Django is importing.
2. **App loading** - Django populates the resolved `INSTALLED_APPS`.
   `ComposeConfig.import_models()` heals the generated runtime in place when the
   rendered source drifts, then imports the concrete model modules so Django
   registers them under the source addon labels.
3. **Serving and lifecycle commands** - stable framework entrypoints such as
   `angee.urls`, `angee.asgi`, `schema`, `resources`, and `rebac sync` read the
   finished Django app registry. Each lifecycle consumes only the app
   declarations or conventional modules it owns.

Settings composition never imports source models. Runtime emission never decides
settings. Normal Django startup heals the runtime in place and never prunes
orphaned labels; destructive reset/prune is reserved for the explicit `angee
build` path.

## Owner Map

| Concern | Owner |
|---|---|
| Project-root discovery, YAML settings load, bootstrap environment | [`angee.compose.settings`](../angee/compose/settings.py) |
| Overridable framework defaults | [`angee.compose.defaults`](../angee/compose/defaults.py) |
| Reserved composed settings and final settings mutation | [`Composer`](../angee/compose/composer.py) |
| Root/dependency graph, app aliases, root annotations | [`AppGraph`](../angee/compose/appgraph.py) |
| Addon settings fragments and declared `ANGEE_*` env overlays | [`AutoConfig`](../angee/compose/autoconfig.py) |
| Addon opt-in and shared dotted-reference resolution | [`angee.addons`](../angee/addons.py) |
| Runtime rendering, drift checks, emission, cleanup, model extension order | [`Runtime`](../angee/compose/runtime.py) |
| Runtime import during Django app population | [`ComposeConfig.import_models()`](../angee/compose/apps.py) |
| HTTP route aggregation | [`angee.urls`](../angee/urls.py) |
| WebSocket routes, HTTP sub-app mounts, mount lifespans | [`angee.asgi`](../angee/asgi.py) |
| GraphQL schema declarations and SDL output | [`angee.graphql`](../angee/graphql/) |
| MCP tool declarations and the `/mcp` StreamableHTTP mount | [`angee.mcp`](../addons/angee/mcp/) |

If a fact belongs to one of these owners, update that owner or its docstring.
This document should point there, not repeat the contract.

## Settings Bootstrap

`angee.compose.settings` is the settings module Django imports. It owns the boot
sequence: find the project root, load or synthesize the project settings module,
apply `django-yamlconf`, reject implicit ancestor settings files, evaluate Angee
defaults, make configured addon roots importable, and call `Composer`.

Project settings may override framework defaults before composition. Settings
that are products of composition are reserved and are assigned by `Composer`; an
addon or project cannot redefine them with a conflicting value. The current
reserved set lives in `COMPOSER_OWNED_SETTINGS`.

`ANGEE_ADDON_DIRS` is interpreted during this phase only to make addon source
roots importable. It is not a second addon list: the project root addon contract
is `INSTALLED_APPS`.

## App Graph And Settings

`Composer` reads `INSTALLED_APPS` as the project's root addon list, resolves the
full ordered app set through `AppGraph`, writes the resolved `AppConfig` objects
back to `INSTALLED_APPS`, and sets the stable framework entrypoints.

`AppGraph` delegates app creation to Django's `AppConfig.create()`, expands each
app's declared dependencies, and annotates the resulting configs with graph facts
that other runtime readers cannot re-derive safely from outside. Dependency
names are app declarations; aliasing, duplicate handling, and cycle validation
belong to `AppGraph`.

Django accepts `AppConfig` instances in `INSTALLED_APPS`, so app loading uses the
same config objects the composer already resolved instead of resolving strings a
second time.

## Autoconfig

After `INSTALLED_APPS` is resolved, `Composer` applies addon settings through
`AutoConfig` in dependency order.

An addon may provide `<app>.autoconfig` with a `SETTINGS` mapping. The keys use
`django-yamlconf` syntax; `AutoConfig` owns the Angee rules around reserved
settings, addon defaults, list/dict merging, and declared `ANGEE_*` environment
overlays. Apps still read `django.conf.settings`; process environment is
normalized during composition.

`django_yamlconf` is installed through the app graph because `angee.compose`
depends on it, so `ycexplain` and `yclist` remain the provenance tools for
composed settings.

Entry-level before/after ordering for list settings is intentionally not built
yet. Current order is addon dependency order plus yamlconf merge semantics. If a
real addon needs item-level ordering later, that belongs in `AutoConfig`, not in
`Composer`.

## Runtime Build And Import

The generated runtime is output, not source. It exists because Django concrete
model classes must live in importable modules with migration packages.

`Runtime.from_django()` reads the installed app registry and runtime settings,
then `Runtime` discovers abstract source models, applies extension bases, renders
the concrete runtime source map, writes or checks that map, redirects migration
modules for emitted labels, and imports generated model modules. Source-model
declarations such as runtime models, extension targets, composition labels, and
extension bases are owned by the base model classes they live on.

`extends` has two runtime shapes:

- `extends = "app.Model"` with `runtime = False` contributes a same-row abstract
  extension base that the composer folds into the target runtime model.
- `extends = "app.Model"` with `runtime = True` emits a materialized Django
  multi-table-inheritance child whose generated concrete class inherits the
  target's generated runtime model and the child source model.

The composer owns only the generated Python runtime source map and explicit
materialization of addon-owned manual migrations. Django owns the resulting
runtime graph, `makemigrations`, execution, rollback, routing, and recording.
GraphQL SDL under `runtime/schemas/` and TypeScript codegen output under
`runtime/gql/` are owned by their GraphQL and frontend codegen lifecycles, so
composer runtime drift checks ignore them.

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

`ComposeConfig.import_models()` is the Django app-loading hook. In app-populate
phase 2 it calls `emit_if_stale()` and then imports generated models:

- `emit_if_stale()` is write-only and idempotent. It repairs missing or stale
  generated sources file by file before import, and it never resets, cleans, or
  materializes addon migrations.
- Orphaned runtime labels left by a removed addon are pruned only by explicit
  `angee build`, which calls the destructive emitter behind the generated
  sentinel guard.
- `angee clean` deletes generated runtime sources only from the configured
  runtime directory, only after verifying Angee's generated sentinel, and it
  preserves migrations.

## Addon Declarations

An Angee addon is a Django app marked by a co-located `addon.toml`. It does not
subclass an Angee base config, and it needs an `apps.py` only to run a Python seam
(`ready()` / `import_models()`); the declarative contract lives in the manifest, and
each lifecycle reads only the declaration it owns.

Routes are conventional: `angee.urls` looks for `urls.py`, and `angee.asgi`
looks for `asgi.py`, but only on apps that are Angee addons (they carry a manifest)
per `angee.addons.is_angee_addon()`. That keeps third-party apps that happen to ship
route modules from leaking into the composed root router.

Other lifecycles remain `addon.toml` facts — declared explicitly, or inferred from
the addon's files when conventional (see the addon-contract guideline). GraphQL
schema declarations are owned by `angee.graphql`; web declarations are owned by the
addon's `addon.toml` (`[web].package` names its rendered package — inferred from
`web/package.json` — and `[web].codegen` declares an external GraphQL codegen pass,
e.g. the operator daemon). The composer is a pure
projector here: it renders `runtime/web/manifest.json` (package graph + codegen
entries) and `runtime/web/tailwind.sources.css` from those static declarations,
holding no schema-name or schema-shape knowledge — the `angee-web-codegen` CLI
owns generating `runtime/gql/<schema>` and the composed `runtime/web/app.ts` from
the SDL on disk. MCP tool declarations are owned by `angee.mcp`; resource and
permission declarations are owned by their base addons. Shared dotted references
use `angee.addons.resolve_addon_reference()` so declaration parsing has one owner.

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
composer-owned. `Runtime` owns `MIGRATION_MODULES` for emitted runtime labels and
preserves unrelated project entries.

## Invariants

- `INSTALLED_APPS` is the project root addon contract.
- There is one resolved Django app set and one boot path.
- Settings are final before Django app loading starts.
- Settings composition does not import source models.
- Runtime emission does not decide settings.
- Normal startup heals the runtime in place but never resets or prunes it.
- Composer drift checks cover composer-owned runtime sources only.
- Apps read `django.conf.settings`; process environment is normalized during
  settings composition.
- Routes are discovered by conventional `urls.py` / `asgi.py`; other lifecycle
  declarations remain explicit AppConfig facts.
- Generated `runtime/` is output; edit addon source, not emitted files.
- Runtime cleanup may delete only the configured generated runtime directory,
  only after verifying Angee's generated sentinel, and must preserve migrations.
