# Backend Guidelines

Backend code is Python, Django, and the composer. It owns data, permissions,
transport-neutral business behavior, and generated contracts.

Follow the shared development process and coding principles in
[`docs/guidelines.md`](../guidelines.md) for every task; the rules below are the
backend-specific layer applied during the Build step.

## Stack

The opinionated stack in `docs/stack.md` is the source of truth for backend
libraries and what each one owns. Check it before adding a dependency or
hand-rolling a concern. Python dependency setup belongs in `pyproject.toml` and
`uv.lock`.

## Django-Native Rule

Angee is not a second framework on top of Django. It is a build-time composer
for Django apps.

Before adding an Angee abstraction, ask: does Django already have an object,
method, or convention that owns this fact?

Use Django's native owners:

- App facts live on `AppConfig`.
- Model behavior lives on models, managers, and querysets.
- Value coercion lives on fields.
- Command dispatch lives in Django management commands and `argparse`.
- Table names, app labels, migrations, and model metadata follow Django
  defaults.

Angee code should own only the composition seam: discovering addons, ordering
them deterministically, emitting runtime apps, merging schemas, syncing
resources, and failing fast on collisions.

A wrapper must prove it adds a real new concept. If it only forwards,
normalizes, or renames a Django object, delete it.

## Package Layering

The framework core is four packages with a one-way dependency rule that a test
enforces:

- `angee.base` is the model foundation (models, fields, mixins, managers,
  querysets, and model emission declarations). It must not import `angee.compose`,
  `angee.graphql`, or addon packages.
- `angee.graphql` is the GraphQL runtime (schema assembly, Strawberry helpers,
  serving, subscriptions, and SDL commands). It may import `angee.base`, never
  `angee.compose`.
- `angee.resources` is the resource subsystem. It may import `angee.base`, never
  `angee.compose`.
- `angee.compose` is the build-time composer. It may import `angee.base` and
  discover plain Django addon configs, but no serving module (`asgi`, `urls`,
  `views`, `consumers`, `signals`, `models`, `graphql`) may import
  `angee.compose`.

Rules that follow from the layering:

- **Addon discovery is a Django app-registry concern**, not a build-only
  concern: serving code such as schema building enumerates Django's installed
  app configs and reads only the declaration attributes it owns. Serving code
  never imports `angee.compose` just to list addons.
- **An Angee addon is a plain Django app config with explicit attributes.**
  Addons do not subclass an Angee base config. `angee_addon = True` opts the app
  into framework addon discovery for conventional routes, and `depends_on` is
  only an ordering contract; each lifecycle step reads only the contract it owns:
  `graphql` reads `schemas`, `resources` reads `resources`, REBAC sync reads
  `permissions`, stable serving imports conventional `urls.py` / `asgi.py`,
  runtime emission reads model-level `runtime = True`, and settings composition reads
  the addon's optional `autoconfig.py`.
- **There is a single app set and a single boot.** `DJANGO_SETTINGS_MODULE`
  points at `angee.compose.settings`, which imports the project's settings
  contract (`settings.yaml` or `settings.py` beside `manage.py`). YAML projects
  declare `INSTALLED_APPS` and `ANGEE_RUNTIME_DIR`; Python projects may declare
  those same facts directly. `angee.compose.settings` loads the project contract
  and calls `Composer(globals()).compose_settings()`, which expands the addon
  dependency closure and sorts the resulting app set, then gives Django the
  resolved `AppConfig` instances in `INSTALLED_APPS`. Framework boot apps
  (`angee.compose`, `angee.base`, `angee.graphql`) arrive through that same graph
  rather than a parallel hardcoded list. In app-populate phase 2,
  `ComposeConfig.import_models()` checks the generated runtime and imports
  concrete model modules before normal app model imports continue. `angee build`
  and `angee clean` may emit stale runtime sources during that hook only so
  Django can finish loading the generated model registry; no build/run app-set
  split exists.
- **The resource ledger is owned by the resource addon.** The composer discovers
  `angee.resources.models.Resource` as a normal addon source model and emits it
  under the `resources` label. `angee.base` must not import `angee.resources`.
- **Refer to an emitted concrete model through the app registry**
  (`apps.get_model("resources", "Resource")`), never by importing the generated
  `runtime/` tree.

## Rules

- Domain behavior lives on models, managers, and querysets.
- Manager/QuerySet canon: chainable read scopes live on a `*QuerySet` exposed
  through `Manager.from_queryset(...)`. Factories and mutations stay on the
  manager that owns the write.
- Source model discovery should follow Django model inheritance and explicit
  model-owned declarations, not naming or field-shape heuristics.
- Put behavior on the object that owns the shape, the Django way: coerce values
  with `Field.to_python`/`get_prep_value` instead of branching on field type from
  outside; ask `model._meta` (`get_field`, `label_lower`) and
  `Field.value_from_object` rather than re-decoding model shape; surface query
  behavior through `Manager.from_queryset`; and give objects classmethod factories
  and `deconstruct`-style methods to construct and serialize themselves. This is
  the backend application of **Find the owner** in `AGENTS.md` and the
  Django-Native Rule above.
- Compose behavior onto the class that owns the data. Settings construction
  belongs on `Composer`; runtime model materialization belongs on `Runtime`.
  Keep a module-level function only for orchestration that genuinely has no
  owner, and prefer forming a cohesive class even then. A dataclass that only
  holds fields while a sibling module mutates and emits from it is a missing
  class. Organizing behavior into named files and classes is what keeps the
  framework consistent and normalized: a class is a fixed home that forces
  related behavior together and resists the drift that loose, scattered
  functions invite.
- Imports go at the top of the module. A function-local or deferred import is a
  smell that a module boundary is wrong — an import cycle, or a layer reaching
  across a seam — so fix the seam (move the shared fact to its owning module, or
  invert the dependency) instead of hiding the import inside a function. Two
  exceptions, both narrow: a dependency that is genuinely optional at runtime
  (isolate it behind its own module), and Django's app-loading order — an
  `AppConfig` module is imported in app-populate phase 1, before the registry is
  ready, so it must defer importing model classes (and signal wiring that pulls
  them in) until a method runs after `ready()`. Mark such a deferral with a
  comment naming the reason; everywhere else, hoist. Within Angee's own source
  (`angee/` and `addons/angee/`) these are
  the only function-local imports allowed — phase-1 deferrals and `TYPE_CHECKING`
  blocks. Probe optional or generated modules with `importlib.util.find_spec`
  (verifying each parent first) rather than `try/except ImportError`, so an absent
  generated `runtime/` reads as "not built yet," not a swallowed error.
- A pure renderer that takes its owner and returns a value with no other state may
  stay a module-level function in the owner's module; make it a method only when
  it reads more than one field of the owner or shares state with sibling helpers.
- A package `__init__.py` whose sole job is re-exporting a stable public API is a
  compatibility surface; `__all__` is allowed there (the usual "avoid `__all__`"
  rule targets ordinary modules).
- When restructuring or lifting existing code, reconstruct each module from its
  contract, tests, and these guidelines — do not paste or mechanically port the
  old code, and do not keep the old modules importable inside the `angee` namespace.
- Source models are abstract. Concrete apps are emitted by the composer.
- Keep Django `Meta` for Django and library-owned options such as
  `rebac_resource_type`; Angee extension facts live on the owning model class.
- `runtime/`, generated schemas, migrations, and codegen stubs are output.
  Change the source, not the artifact.
- REBAC is structural and owned by `django-zed-rebac`. Addons declare
  `permissions.zed` beside the owning app. Permission sync is the library's
  own `manage.py rebac sync`. Use the library's
  field-backed relations (`// rebac:field=...`) when a relationship is already
  represented by a Django FK or one-to-one field.
- GraphQL authoring is native Strawberry. Addons expose a `schemas` mapping in
  conventional `schema.py` modules. Each named schema contributes Strawberry
  types into fixed buckets (`query`, `mutation`, `subscription`, `types`,
  `extensions`); Angee merges buckets across addons and builds one Strawberry
  `Schema` per name.
- Use symbolic model references across addon boundaries; avoid import cycles.
- Build output must be byte-deterministic.

## Framework Contracts

Framework contracts should be self-explaining in code. Add docstrings to public
modules, classes, methods, functions, declarative manifest attributes, and public
module-level constants. Add docstrings to private helpers when their role is not
obvious from the function name and signature. Do not maintain a parallel spec, field inventory, or model
API list for behavior that can live clearly beside the code.

`AppConfig` is the addon manifest and owns addon-local interpretation. Use
Django's own facts before adding an Angee fact: the addon root is
`AppConfig.path`, source models live in `models.py`, and GraphQL contributions
live in `schema.py`. Put validation, normalization, and path resolution for one
addon on the `AppConfig` subclass. Prefer methods on the object that owns the
data — the `AppConfig` for one addon, a runtime build object for composition —
over loose functions; keep a function loose only for orchestration no single
object owns. Put current manifest attributes and their exact authoring forms in
the `AppConfig` base class docstrings, not in this guideline.

Before decomposing backend code, classify each fact by its Django owner:

- Persisted choices live beside the model field, usually as model-owned
  `TextChoices`.
- Row-set behavior lives on managers and querysets.
- Instance behavior lives on model methods and properties.
- Addon declaration and path-resolution behavior lives on `AppConfig`.
- Management commands parse arguments and dispatch to the owning model, manager,
  service, or composer function.
- Compatibility facades exist only for an explicit compatibility promise.

The project settings contract declares project facts; Angee owns Django
composition wiring. By default, keep `settings.yaml` beside `manage.py` and set
only the deliberate composition facts there, especially `INSTALLED_APPS` and
`ANGEE_ADDON_DIRS` / `ANGEE_RUNTIME_DIR`. `ANGEE_PROJECT_SETTINGS` may point at a
project Python settings module when the project needs one. `angee.compose.settings`
loads Python settings first, overlays `settings.yaml` with django-yamlconf,
evaluates `angee.compose.defaults` as the base Django settings module, and asks
`Composer(globals()).compose_settings()` to compose `INSTALLED_APPS`,
`MIGRATION_MODULES`, import paths, and addon autoconfig.
Addon autoconfig uses yamlconf-style `SETTINGS` keys: plain keys are defaults,
`:append` / `:prepend` keys always merge, dotted keys update nested dictionaries,
`:raw` protects literal braces, and declared `ANGEE_*` addon settings may be
overlaid by same-named process environment values from the stack. Use
`settings.py` only when the project truly needs Python-computed settings. Angee
treats yamlconf errors as Django configuration failures and rejects implicit
ancestor `settings.yaml` files; only the project file and an explicit
`YAMLCONF_CONFFILE` may contribute file-backed settings. Generic typed yamlconf
environment overrides still require `:jsonenv`.
Anchor project defaults to `BASE_DIR`, never to the current working directory.

Keep `angee` as a namespace package. Do not add an `__init__.py` at either
namespace root (`angee/` for the framework, `addons/angee/` for the base
addons); split addon distributions must be able to contribute packages under the
shared `angee.*` namespace.

Avoid `__all__` unless a module has a concrete star-import or compatibility
requirement. Public API should usually be obvious from module names, object
names, and docstrings.

## Naming

Naming is structural: Django and the composer both locate code by name, so a
wrong name is a broken contract, not a style nit. Django is the reference — match
it exactly.

- **Modules** are lowercase, single-word, named by role: `models.py`,
  `managers.py`, `admin.py`, `forms.py`, `urls.py`, `apps.py`, `signals.py`,
  `mixins.py`, `validators.py`, `fields.py`, `backends.py`.
- **Structural directories** are fixed and discovered by name — never rename them:
  `migrations/`, `management/commands/`, `templatetags/`, `templates/`,
  `backends/`.
- **Packages / addons** are short and lowercase — no CamelCase, no stray
  underscores (`auth`, `contenttypes`, `storage`) — and match the addon label.
- **Classes** are PascalCase with a role suffix that mirrors the module: `*Field`,
  `*Mixin`, `*Manager`, `*QuerySet`, `*Form`, `*Admin`, and `*Config` for the
  `AppConfig`.
- **Methods / functions** are snake_case and verb-first from a stable vocabulary:
  `get_*` (accessors), `is_*` / `has_*` (booleans), `as_*` / `to_*` / `from_*`
  (conversions), `create_*` / `save_*` / `delete_*` (mutations);
  `_leading_underscore` for internal. Settings and constants are `UPPER_SNAKE`.
- **camelCase only when extending an external API that uses it** (e.g. Django's
  `unittest` assertions). Otherwise never.

## Checks

Run the narrowest relevant check while editing, then the broad check before
handoff:

```sh
uv run ruff check . --no-cache
uv run mypy angee addons
uv run vulture
uv run pytest
uv run examples/notes-angee/manage.py angee build --check
```

If a command is not wired yet, say so plainly.
