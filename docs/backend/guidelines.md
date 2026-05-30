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

## Rules

- Domain behavior lives on models, managers, and querysets.
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
- Compose behavior onto the class that owns the data. When several functions
  take the same object and read, transform, or emit from it, that object should
  be a class and those functions its methods — the runtime build owns its own
  plan/emit/check/reset (e.g. an `AngeeRuntime` object), not a module of loose
  functions wrapped around a passive dataclass. Keep a module-level function only
  for orchestration that genuinely has no owner, and prefer forming a cohesive
  class even then. A dataclass that only holds fields while a sibling module
  mutates and emits from it is a missing class. Organizing behavior into named
  files and classes is what keeps the framework consistent and normalized: a
  class is a fixed home that forces related behavior together and resists the
  drift that loose, scattered functions invite.
- Imports go at the top of the module. A function-local or deferred import is a
  smell that a module boundary is wrong — an import cycle, or a layer reaching
  across a seam — so fix the seam (move the shared fact to its owning module, or
  invert the dependency) instead of hiding the import inside a function. Two
  exceptions, both narrow: a dependency that is genuinely optional at runtime
  (isolate it behind its own module), and Django's app-loading order — an
  `AppConfig` module is imported in app-populate phase 1, before the registry is
  ready, so it must defer importing model classes (and signal wiring that pulls
  them in) until a method runs after `ready()`. Mark such a deferral with a
  comment naming the reason; everywhere else, hoist.
- Source models are abstract. Concrete apps are emitted by the composer.
- Keep Django `Meta` for Django and library-owned options such as
  `rebac_resource_type`; Angee extension facts live on the owning model class.
- `runtime/`, generated schemas, migrations, and codegen stubs are output.
  Change the source, not the artifact.
- REBAC is structural and owned by `django-zed-rebac`. Addons declare
  `permissions.zed`; Angee wires schema sync and only adds build-time review
  output. Use the library's field-backed relations (`// rebac:field=...`) when a
  relationship is already represented by a Django FK or one-to-one field.
- GraphQL authoring is native Strawberry. Addons expose Strawberry `Schema`
  objects from conventional `graphql.py` modules, and the composer only
  discovers named schemas.
- Use symbolic model references across addon boundaries; avoid import cycles.
- Build output must be byte-deterministic.

## Framework Contracts

Framework contracts should be self-explaining in code. Add docstrings to public
modules, classes, methods, functions, and declarative manifest attributes. Add
docstrings to private helpers when their role is not obvious from the function
name and signature. Do not maintain a parallel spec, field inventory, or model
API list for behavior that can live clearly beside the code.

`AppConfig` is the addon manifest and owns addon-local interpretation. Use
Django's own facts before adding an Angee fact: the addon root is
`AppConfig.path`, source models live in `models.py`, and GraphQL contributions
live in `graphql.py`. Put validation, normalization, and path resolution for one
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

Settings helpers are pure functions of their arguments: they return plain
Django setting mappings and do not read the environment. Do not pass `globals()`
into framework code or let helpers mutate a settings module from the outside;
the host may apply the returned mapping in one visible step. The host owns where
runtime and data live — it resolves any `ANGEE_RUNTIME_DIR` / `ANGEE_DATA_DIR`
override and passes explicit paths to the helper. Anchor host defaults to a
fixed location via `__file__` (e.g. the repo-root control directory), never to
the current working directory.

Keep `angee` as a namespace package. Do not add `src/angee/__init__.py`; split
addon distributions must be able to contribute packages under the shared
`angee.*` namespace.

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
uv run ruff check .
uv run mypy src/
uv run pytest
angee build --check
```

If a command is not wired yet, say so plainly.
