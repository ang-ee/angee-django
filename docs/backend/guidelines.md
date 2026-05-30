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

## Rules

- Domain behavior lives on models, managers, and querysets.
- Put behavior on the object that owns the shape, the Django way: coerce values
  with `Field.to_python`/`get_prep_value` instead of branching on field type from
  outside; ask `model._meta` (`get_field`, `label_lower`) and
  `Field.value_from_object` rather than re-decoding model shape; surface query
  behavior through `Manager.from_queryset`; and give objects classmethod factories
  and `deconstruct`-style methods to construct and serialize themselves. Reserve
  module-level functions for cross-object orchestration — discovery, ordering,
  emission, conflict checks.
- Source models are abstract. Concrete apps are emitted by the composer.
- Keep Django `Meta` for Django and library-owned options such as
  `rebac_resource_type`; Angee extension facts live on the model class, for
  example `extends = "notes.Note"`.
- `runtime/`, generated schemas, migrations, and codegen stubs are output.
  Change the source, not the artifact.
- REBAC is structural and owned by `django-zed-rebac`. Addons declare
  `permissions.zed`; Angee wires schema sync and only adds build-time review
  output. Use the library's field-backed relations (`// rebac:field=...`) when a
  relationship is already represented by a Django FK or one-to-one field.
- GraphQL authoring is native Strawberry. Addons expose plain Strawberry classes
  from conventional `graphql.py` modules, and the composer only merges them into
  named schemas.
- Use symbolic model references across addon boundaries; avoid import cycles.
- Build output must be byte-deterministic.

## Framework Contracts

Framework contracts should be self-explaining in code. Add docstrings to public
modules, classes, methods, functions, and declarative manifest attributes. Add
docstrings to private helpers when their role is not obvious from the function
name and signature. Do not maintain a parallel spec for behavior that can live
clearly beside the code.

`AppConfig` is the addon manifest and owns addon-local interpretation. Use
Django's own facts before adding an Angee fact: the addon root is
`AppConfig.path`, source models live in `models.py`, and GraphQL contributions
live in `graphql.py`. Put validation, normalization, and path resolution for one
addon on the `AppConfig` subclass. Keep loose functions for cross-addon
orchestration such as discovery, ordering, emission, and conflict checks. Do not
add unused helper methods, registries, or derived path accessors. The current
base manifest fields are:

- `depends_on`: addon labels or app names that must compose first.
- `rebac_schema`: a `django-zed-rebac` schema file, defaulting to
  `permissions.zed` and skipped when absent.
- `resources`: a dict of resource tiers to explicit file lists. Empty tiers are
  the default; addons list files such as `resources/demo/010_notes.note.yaml`.

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
