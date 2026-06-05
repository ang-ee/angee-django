# The Composer

The composer is Angee's Django composition hook. It turns project settings and
addon packages into one normal Django app: final settings, installed apps,
generated concrete models, URL routes, ASGI routes, GraphQL schemas, resources,
and permission artifacts.

## Two Hooks

Composition enters Django twice.

1. **Settings hook** — Django imports `angee.compose.settings`.
   This module finds the project settings contract (`settings.yaml` or
   `settings.py` beside `manage.py`, with `ANGEE_PROJECT_DIR` and
   `ANGEE_PROJECT_SETTINGS` available for non-default layouts), makes the
   project importable, loads project settings with `django-yamlconf`, evaluates
   Angee defaults, then calls `Composer(globals()).compose_settings()` to finish
   the Django settings namespace in place.
2. **App-loading hook** — Django reaches `angee.compose.apps.ComposeConfig`
   during `apps.populate()`. Its `import_models()` hook calls
   `Runtime.from_django().materialize_models()` to render and emit
   `runtime/<label>/models.py`, then import the generated model modules so
   concrete models register under their owning addon labels.

Settings are final before Django starts app loading. Runtime model emission
happens only during app loading, never while settings are being built.

## Settings Phase

`angee.compose.settings` is a normal Django settings module. After loading
project settings, it asks `Composer(globals()).compose_settings()` to produce:

- `INSTALLED_APPS`
- `MIDDLEWARE`
- `AUTHENTICATION_BACKENDS`
- `MIGRATION_MODULES`
- `ROOT_URLCONF = "angee.urls"`
- `ASGI_APPLICATION = "angee.asgi.application"`
- Angee and library defaults not explicitly overridden by the project

Angee treats `django-yamlconf` errors as composition errors. Malformed YAML,
bad `{REF}` expansion, recursive references, invalid dotted keys, and invalid
merge types fail settings import. Angee also rejects implicit ancestor
`settings.yaml` files; only the project `settings.yaml` and an explicit
`YAMLCONF_CONFFILE` may contribute file-backed settings.

Boot environment reads use `django-environ`. `ANGEE_PROJECT_DIR` points at the
project root, `ANGEE_PROJECT_SETTINGS` names the Python settings module
(default `settings`), and `ANGEE_ADDON_DIRS` lists project addon source roots.
When both Python settings and `settings.yaml` exist, Python settings seed the
module and YAML overlays it.

The project declares root apps with Django's `INSTALLED_APPS`. The composer
reads that setting from the namespace it is initializing, resolves entries
through Django `AppConfig.create()`, expands each addon's `depends_on`, orders
the app graph deterministically, and emits `INSTALLED_APPS` as those
`AppConfig` instances. Django accepts config instances in `INSTALLED_APPS`, so
`apps.populate()` uses them directly instead of calling `AppConfig.create()` a
second time.

Each installed app may also provide `autoconfig.py`. `AutoConfig` applies each
app's `SETTINGS` mapping in dependency order through `django-yamlconf`, so
yamlconf owns environment overrides, provenance, dotted keys, and
`:append` / `:prepend` merging. A plain key is a default and is skipped when the
setting already exists; a marked key always merges:

- `AUTH_USER_MODEL` sets a default when absent
- `MIDDLEWARE:append` appends middleware entries
- `DATABASES.default.OPTIONS.timeout` updates one nested dictionary value
- `ANGEE_GRAPHQL_IDE` is contributed by the GraphQL addon

Strings containing literal braces must use yamlconf's `:raw` marker. Typed
environment overrides must declare `FOO:jsonenv: true`; otherwise
`YAMLCONF_FOO` is a string. `django_yamlconf` is installed in the composed app
set, so `ycexplain` and `yclist` are the provenance tools for composed settings.

Addon order from `depends_on` gives the contribution order. Entry-level
before/after ordering is intentionally deferred; if it becomes necessary, it
belongs in `AutoConfig` as an `OrderingRelationship(..., add_missing=False)`
style layer, not in `Composer`.

## App-Loading Phase

The app-loading phase composes ORM models.

`Runtime.from_django().materialize_models()` reads the installed `AppConfig`
objects from Django's app registry, collects abstract source models, applies
`extends`, checks field collisions, renders concrete model modules under
`runtime/<label>/models.py`, writes stale files, and imports the generated model
modules.

The generated runtime package is output, not source. It exists because Django
requires concrete model classes to live in a real importable module with a real
migrations package. Migrations live under `runtime/<label>/migrations/` and are
owned by Django's migration machinery.

## Addon Contract

An addon is a plain Django app. Its `AppConfig` declares package identity,
ordering, and the lifecycle declarations it contributes:

- `depends_on` for app ordering
- `emits_runtime_models` when the runtime should materialize its abstract source
  models
- `schemas`, `url_patterns`, `asgi_websocket_urlpatterns`, `resources`, and
  `permissions` when that lifecycle has work to do

Those declarations point at conventional addon modules and files:

- `models.py` for abstract model sources
- `schema.py` for GraphQL schema contributions
- `urls.py` for HTTP route contributions
- `asgi.py` for ASGI or websocket route contributions
- `resources/` for resource data files
- `permissions.zed` for REBAC permissions

Settings contributions live in optional `autoconfig.py`, not on `AppConfig`.

## Serving

`angee.urls` and `angee.asgi` are stable framework entrypoints, not generated
runtime files. They use the same composed Django settings and include route
contributions from installed addon modules after Django has built the app
registry.

GraphQL, resources, permissions, and other lifecycles follow the same rule: each
pass reads only the app config declarations it owns.

## Invariants

- There is one Django app set and one boot path.
- No `ANGEE_BUILD` flag and no build/run split.
- Settings composition does not import source models.
- Runtime emission does not decide settings.
- Generated `runtime/` is output; edit addon source, not emitted files.
- Destructive cleanup may touch only the configured generated runtime directory
  after verifying Angee's generated sentinel, and it must preserve migrations.
