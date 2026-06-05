# The Composer

The composer is Angee's Django composition layer. It turns a project's normal
Django settings contract and installed addon apps into one runnable Django
project: final settings, a single ordered app registry, generated concrete ORM
models, stable URL/ASGI entrypoints, and lifecycle inputs for GraphQL,
resources, permissions, and other addons.

The important constraint is that Angee stays Django-shaped. A project declares
root addons with `INSTALLED_APPS`; addons are plain Django apps with extra
`AppConfig` attributes for explicit lifecycle declarations; HTTP and WebSocket
routes use Django-style conventional modules. Settings are ready before Django
app loading; generated models are imported during Django app loading.

## Flow

Composition has three phases.

1. **Settings bootstrap** — Django imports `angee.compose.settings`.
   This module finds the project root, loads project settings, applies Angee
   defaults, resolves the addon graph, and mutates the settings namespace that
   Django is importing.
2. **App loading** — Django populates the resolved `INSTALLED_APPS`.
   When it reaches `angee.compose.apps.ComposeConfig.import_models()`, Angee
   heals the generated runtime in place (`emit_if_stale`, write-only) and then
   imports the concrete model modules so Django registers them under the source
   addon labels — the runtime is always freshly rendered before it is loaded.
3. **Serving and lifecycle commands** — stable framework entrypoints such as
   `angee.urls`, `angee.asgi`, `schema`, `resources`, and `rebac sync` read the
   finished Django app registry. URL/ASGI serving imports conventional
   `urls.py` / `asgi.py`; GraphQL, resources, permissions, runtime emission,
   and settings autoconfig consume only the `AppConfig` declarations or addon
   modules they own.

Settings composition never imports source models. Runtime emission never decides
settings. Normal Django startup heals the runtime in place (`emit_if_stale`,
write-only, never resetting or pruning) and then imports it; the destructive
reset/prune of orphaned labels belongs to the explicit `angee build`.

## Settings Bootstrap

`angee.compose.settings` is a normal Django settings module. It runs linearly:

1. Resolve the project root.
   `ANGEE_PROJECT_DIR` wins. Otherwise a `manage.py` invocation uses the
   directory containing `manage.py`. For direct imports, such as `daphne
   angee.asgi:application`, the current working directory is accepted when it
   contains `settings.yaml` or `settings.py`.
2. Resolve the project settings module.
   `ANGEE_PROJECT_SETTINGS` names it, defaulting to `settings`. If a
   `settings.py` exists, Angee imports it. If only `settings.yaml` exists, Angee
   synthesizes a `settings` module so Django still sees a normal settings
   module.
3. Prepend the project root to `sys.path`.
   This makes project `settings.py` importable before Python settings are
   loaded. Addon source roots are not guessed here; they come later from the
   `ANGEE_ADDON_DIRS` setting.
4. Load project YAML through `django-yamlconf`.
   Python settings seed the module and `settings.yaml` overlays them. `BASE_DIR`
   is the project-root reference used by YAML expansion.
5. Reject unsafe yamlconf behavior.
   Angee turns `django_yamlconf` logged errors into `ImproperlyConfigured`, so
   malformed YAML, bad `{REF}` expansion, recursive references, invalid dotted
   keys, and invalid `:append` / `:prepend` types fail settings import. Angee
   also rejects yamlconf's implicit ancestor `settings.yaml` cascade; only the
   project `settings.yaml`, `**INTERNAL**`, `**ENVIRONMENT**`, and an explicit
   `YAMLCONF_CONFFILE` may contribute settings.
6. Evaluate `angee.compose.defaults`.
   Defaults are evaluated with project settings as the seed, then copied into
   the importing settings namespace.
7. Prepend configured addon roots.
   `ANGEE_ADDON_DIRS` is now known, so those directories and the project root
   are put on `sys.path`.
8. Call `Composer(globals()).compose_settings()`.

`angee.compose.defaults` owns overridable framework defaults:

- `BASE_DIR`
- `DEBUG`
- `ALLOWED_HOSTS`
- `USE_TZ`
- `DEFAULT_AUTO_FIELD`
- `INSTALLED_APPS` with `angee.compose` prepended
- `ANGEE_RUNTIME_MODULE`
- `ANGEE_DATA_DIR`
- `ANGEE_ADDON_DIRS`
- `STATIC_URL`, `STATIC_ROOT`, `MEDIA_URL`, `MEDIA_ROOT`
- default SQLite `DATABASES`

Project settings may override these defaults before the composer runs. Reserved
composition invariants are not defaults; they are owned by `Composer`.

## Composer

`Composer` receives the already-loaded settings namespace and finishes the
settings that depend on the addon graph.

It reads `INSTALLED_APPS` as the project's root addon list. There is no separate
`ANGEE_ADDONS` list. Entries may be normal Django app strings or already-created
`AppConfig` instances.

It requires `ANGEE_RUNTIME_DIR`, normalizes it to a `Path`, and then asks
`AppGraph` to resolve the full ordered app set.

`AppGraph` owns app dependency resolution:

- creates apps through Django's `AppConfig.create()`
- registers aliases by full app name and Django app label
- expands each config's `depends_on` closure
- allows dependencies to refer to either app name or label
- rejects duplicate apps and duplicate aliases
- rejects unknown dependencies as `ImproperlyConfigured`
- rejects dependency cycles as `ImproperlyConfigured`
- sorts dependency traversal so emitted settings are deterministic

After graph resolution, `Composer` writes the final settings:

- `INSTALLED_APPS` becomes the resolved list of `AppConfig` instances
- `ROOT_URLCONF = "angee.urls"`
- `ASGI_APPLICATION = "angee.asgi.application"`
- `ANGEE_RUNTIME_DIR` becomes the normalized runtime path
- the runtime parent directory is prepended to `sys.path`

Django accepts `AppConfig` instances in `INSTALLED_APPS`, so Django app loading
uses the exact config objects the composer already resolved instead of resolving
strings a second time.

These settings are composer-owned and project/addon autoconfig may not redefine
them with conflicting values:

- `ANGEE_RUNTIME_DIR`
- `ASGI_APPLICATION`
- `INSTALLED_APPS`
- `ROOT_URLCONF`

## Autoconfig

After `INSTALLED_APPS` is resolved, `Composer` applies addon settings through
`AutoConfig` in dependency order.

Each app may provide an optional `<app>.autoconfig` module with a `SETTINGS`
mapping. Keys use `django-yamlconf` syntax directly:

```python
SETTINGS = {
    "AUTH_USER_MODEL": "iam.User",
    "MIDDLEWARE:append": ["django.middleware.common.CommonMiddleware"],
    "AUTHENTICATION_BACKENDS:append": ["angee.iam.auth.AngeeBackend"],
    "DATABASES.default.OPTIONS.timeout": 20,
    "ANGEE_GRAPHQL_IDE": "graphiql",
}
```

Rules:

- `SETTINGS` must be a mapping.
- Composer-owned settings are rejected.
- A plain top-level key is a default; if the setting already exists from
  project settings, YAML, environment, defaults, or an earlier addon, the addon
  default is skipped.
- `:append` and `:prepend` always merge.
- Dotted keys update nested dictionaries without replacing the whole parent.
- Literal braces in string settings require yamlconf's `:raw` marker.
- Generic typed yamlconf env overrides require `FOO:jsonenv: true`; otherwise
  `YAMLCONF_FOO` is a string.

Declared Angee settings may also come from the process environment without
exposing yamlconf's prefix in stack files. If an addon declares a top-level
setting whose name starts with `ANGEE_`, and the same name exists in
`os.environ`, `AutoConfig` overlays that value through `django-yamlconf` into
the Django settings namespace. Apps still read `django.conf.settings`; app code
does not read `os.environ`.

For example, the operator addon declares `ANGEE_OPERATOR_URL` and
`ANGEE_OPERATOR_TOKEN` defaults in its autoconfig. A dev stack may export
`ANGEE_OPERATOR_URL` and `ANGEE_OPERATOR_TOKEN`; Angee turns those into Django
settings during composition. A project may also set the same keys in
`settings.yaml`.

`django_yamlconf` is installed through the app graph because `angee.compose`
depends on it, so `ycexplain` and `yclist` are the provenance tools for
composed settings.

Entry-level before/after ordering for list settings is intentionally not built
yet. Current order is addon dependency order plus `:append` / `:prepend`. If a
real addon needs item-level ordering later, that belongs in `AutoConfig`, not in
`Composer`.

## Runtime Build And Import

The generated runtime is output, not source. It exists because Django concrete
model classes must live in importable modules with migration packages.

`Runtime.from_django()` reads:

- installed app configs from Django's app registry
- `ANGEE_RUNTIME_DIR`
- `ANGEE_RUNTIME_MODULE`, defaulting to `runtime`

`Runtime` imports installed model modules during Django's model import phase,
groups abstract source models with `runtime = True` by app label, applies
`extends` extension bases, rejects field collisions, redirects
`MIGRATION_MODULES` for emitted labels, renders concrete model source, emits
model decorators declared by composed bases, and imports generated model modules.

Generated files live under `runtime/`:

- `runtime/__init__.py`
- `runtime/<label>/__init__.py`
- `runtime/<label>/models.py`
- `runtime/<label>/migrations/__init__.py`

Migration files themselves are not generated by Angee. Django's
`makemigrations` owns `runtime/<label>/migrations/`, and Angee cleanup preserves
migrations.

`ComposeConfig.import_models()` is the Django app-loading hook. In app-populate
phase 2 it calls `emit_if_stale()` and then imports the generated models:

- `emit_if_stale()` renders the sources and, when the on-disk runtime drifts,
  heals it file by file. It is write-only and idempotent — it never resets or
  cleans — so a fresh, drifted, or partially-deleted runtime is repaired before
  it is imported. The runtime is always freshly rendered from the abstract
  sources before it is loaded; there is no build/run app-set split, and a
  missing runtime never surfaces as a cryptic `AUTH_USER_MODEL` resolution
  error.
- Orphaned files left by a removed addon are pruned only by the explicit
  `angee build` (which calls the destructive `emit`), never by the boot hook.

The `angee build` command runs after Django setup and emits only if stale.
`angee build --check` reports a stale runtime as a `CommandError` without
writing. `angee clean` deletes generated runtime sources behind the generated
sentinel guard and preserves migrations; when preserved migrations remain, the
root generated sentinel stays as the cleanup authorization marker.

## Addon AppConfig Contract

An Angee addon is a plain Django app. It does not subclass an Angee base config.
Each lifecycle reads the smallest contract it owns. Routes use conventional
modules; GraphQL, resources, permissions, ordering, and runtime emission remain
explicit `AppConfig` declarations.

Common attributes:

- `angee_addon`: `True` opts the app into Angee addon discovery for conventional
  URL and ASGI route modules
- `depends_on`: app names, or labels already present in the resolved app graph,
  that must load before this app
- `schemas`: GraphQL schema contribution declaration, owned by
  `angee.graphql`
- `resources`: resource file declarations, owned by `angee.resources`
- `permissions`: permission file declaration, owned by REBAC sync

Conventions:

- abstract source models live in `models.py`
- source models that materialize declare `runtime = True`; extension models
  declare `extends = "app.Model"`
- GraphQL schema contributions usually live in `schema.py`
- HTTP route contributions live in `urls.py` as iterable `urlpatterns`
- WebSocket route contributions live in `asgi.py` as iterable
  `websocket_urlpatterns` or a callable returning them
- resource data usually lives under `resources/`
- permission declarations usually live in `permissions.zed`
- settings contributions live in optional `autoconfig.py`

Route modules are read only from apps that opt in with `angee_addon = True`
(`angee.addons.is_angee_addon`), so third-party apps that happen to ship a
`urls.py` / `asgi.py` do not leak routes into the Angee root router.

The GraphQL addon is the routing example:

```python
class GraphQLConfig(AppConfig):
    name = "angee.graphql"
    depends_on = ("angee.base", "channels", "daphne")
    schemas = None
```

The IAM addon is the explicit declaration example:

```python
class IAMConfig(AppConfig):
    name = "angee.iam"
    depends_on = ("angee.resources", "angee.graphql", "django.contrib.auth")
    schemas = "schema.schemas"
    permissions = "permissions.zed"
    resources = {
        "master": ({"path": "resources/master/010_iam.vendor.yaml", "adopt": "slug"},),
    }
```

## Serving

`angee.urls` and `angee.asgi` are stable framework entrypoints. They are not
generated into the runtime directory.

`angee.urls` walks `apps.get_app_configs()` in the composed dependency order.
For each app with a `urls.py` module, it imports the module and extends the
global `urlpatterns` with that module's `urlpatterns` when present.

`angee.asgi` supports direct Daphne imports. It sets default
`DJANGO_SETTINGS_MODULE=angee.compose.settings`, discovers `ANGEE_PROJECT_DIR`
from the environment or a nearby project settings file, builds Django's ASGI
application, then wraps it in Channels routing only when installed addons
contribute WebSocket patterns through `asgi.py`.

Other lifecycle commands follow the same shape: they enumerate Django's app
registry and consume only the AppConfig declarations or conventional route
modules they own. The composer does not keep a parallel addon registry.

## Example Project Shape

A minimal YAML project declares roots and project-owned paths:

```yaml
SECRET_KEY: notes-example-dev-key
DEBUG: true
ALLOWED_HOSTS:
  - "*"

INSTALLED_APPS:
  - angee.integrate
  - angee.operator
  - example.notes

ANGEE_ADDON_DIRS:
  - "{BASE_DIR}/addons"
ANGEE_RUNTIME_DIR: "{BASE_DIR}/runtime"
ANGEE_DATA_DIR: "{BASE_DIR}/../../.angee/data"
```

The project does not declare `ROOT_URLCONF` or `ASGI_APPLICATION`; those are
composer-owned. `Runtime` owns `MIGRATION_MODULES` for emitted runtime labels and
preserves unrelated project entries.

## Invariants

- `INSTALLED_APPS` is the project root addon contract.
- There is one resolved Django app set and one boot path.
- Settings are final before Django app loading starts.
- Settings composition does not import source models.
- Runtime emission does not decide settings.
- Normal startup heals the runtime in place but never resets or prunes it.
- Apps read `django.conf.settings`; process environment is normalized during
  settings composition.
- Routes are discovered by conventional `urls.py` / `asgi.py`; other lifecycle
  declarations remain explicit AppConfig facts.
- Generated `runtime/` is output; edit addon source, not emitted files.
- Runtime cleanup may delete only the configured generated runtime directory,
  only after verifying Angee's generated sentinel, and must preserve migrations.
