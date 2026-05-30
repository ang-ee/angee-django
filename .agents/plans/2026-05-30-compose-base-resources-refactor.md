# Compose / Base / Resources Refactor — Implementation Plan

> **For the executing agent (Codex):** This is a clean **rewrite**, not a port.
> Do **NOT** copy code from the old tree. Read each module's *contract* below and
> the guidelines (`AGENTS.md`, `docs/guidelines.md`, `docs/backend/guidelines.md`,
> `docs/stack.md`, `docs/glossary.md`), then write each module fresh, decomposing
> behavior into classes and methods per the guidelines. The old tree is kept at
> `src/angee/_base_old/` as a **behavioral reference only** (what it does, the
> edge cases it handles) — never as a source to paste. It is deleted in the final
> slice. Execute **slice by slice**, verifying each before the next.

**Goal:** Restructure the framework core from one `angee.base` package into three
clean packages — `angee.compose` (build-time), `angee.base` (runtime), and
`angee.resources` (resource subsystem) — dropping `.angee-manifest.json` and the
build flag, composing behavior onto classes, and putting every import at module
top, with the whole tree rewritten to the guidelines.

**Architecture:** Three layers with a one-way dependency rule:
`resources → base` and `compose → base` (+ `compose` uses `resources` only as a
build input via discovery). `base` is pure runtime and imports neither. The build
becomes **emit-only**; `makemigrations`/`migrate` run as a separate later step, so
no "is a build running" flag is needed. `Resource` is emitted under the `base` app
label via the `source_model_modules` composition seam, keeping `base.Resource`.

**Tech Stack:** Python 3.14, Django 6, strawberry-django, channels/daphne,
django-zed-rebac, django-import-export, django-reversion, django-simple-history,
django-sqids, tablib, pytest-django, ruff, mypy, uv.

---

## 1. Target architecture

Three sibling packages under the `angee` namespace package (no
`src/angee/__init__.py`). Every public module/class/method/function and every
declarative manifest attribute gets a docstring; private helpers get one when
their role is not obvious from name + signature.

### 1.1 `angee.base` — runtime (imports neither compose nor resources)

| Module | Responsibility |
|---|---|
| `base/apps.py` | `BaseAddonConfig` (the addon contract) + `BaseConfig`. The contract exposes addon facts as cached_property: `model_classes`, `model_extensions`, `schema_parts`, `rebac_schema_path`, `resource_manifest`, `dependencies`, `source_models_module`, `graphql_module`. Declares `source_model_modules`, `depends_on`, `rebac_schema`, `resources`. `import_models()` adoption stays (loads `runtime/<label>/models.py`). `BaseConfig` (label `base`) sets `source_model_modules = ("angee.resources.models",)` and wires `register_revision_models` from `ready()`. |
| `base/mixins.py` | `TimestampMixin`, `SqidMixin`, `HistoryMixin`, `RevisionMixin`. |
| `base/models.py` | `AngeeModel` (abstract base; composition/extension classmethods, `public_id`/`from_public_id` typed `Self`), `instance_from_public_id`, `public_id_of` (AngeeModel-or-plain duality, in ONE place). |
| `base/signals.py` | Change publishers (`connect_publishers`, `_on_save`, `_on_delete`, `_publish`, `_broadcast`, `change_group`, `_json_safe`) + `register_revision_models`. |
| `base/graphql/__init__.py` | Re-export `crud`, `changes`, `ChangeEvent`, schema helpers. |
| `base/graphql/introspection.py` | `surface_name`, `surface_field_names`, `django_model` (the only place that reads Strawberry internals). |
| `base/graphql/crud.py` | `crud(...)` factory + `DeletePreview`/`collect_delete_preview`. |
| `base/graphql/subscriptions.py` | `changes(...)` + `ChangeEvent` + REBAC gating + actor resolution. |
| `base/graphql/schema.py` | Named-schema composition (`build_schema`, `collect_schema_*`, `render_sdl`). Consider a small owner class (see §2.4). |
| `base/views.py` | `graphql_endpoint` + cached `_get_view`. |
| `base/consumers.py` | `AngeeGraphQLWSConsumer`. |
| `base/asgi.py` | `build_application()` routing only. |
| `base/urls.py` | `urlpatterns` only. |
| `base/settings.py` | `compose_defaults(...)` host serve-settings helper (pure; adds `angee.resources` to INSTALLED_APPS; no `.angee-manifest` anything). |

### 1.2 `angee.resources` — resource subsystem (imports `base`; never `compose`)

A plain Django app (has an `AppConfig` so its management command is discoverable;
it is **not** an Angee source addon — `discover_addons` only collects
`BaseAddonConfig` instances). Its abstract `Resource` source model is pulled into
the `base` label by `BaseConfig.source_model_modules`, so the emitted concrete
model is `base.Resource`.

| Module | Responsibility |
|---|---|
| `resources/apps.py` | Plain `AppConfig` (`name = "angee.resources"`) so the command is discovered. |
| `resources/exceptions.py` | `ResourceLoadError` (leaf; no intra-package imports). |
| `resources/tiers.py` | `ResourceTier` TextChoices + `from_value` (leaf). |
| `resources/entries.py` | `ResourceEntry` (with `adopt: bool = False`), `ResourceRow`, `ResourceGroup`, `LoadResult`, `ValidationResult`, `resolve_model`, format/declaration value types. |
| `resources/ordering.py` | `order_entries` (depends_on topo-sort). |
| `resources/fetch.py` | `fetch_url` (http/https cache). |
| `resources/widgets.py` | `XrefForeignKeyWidget`/`XrefManyToManyWidget` (+ `XrefWidgetMixin` carrying the ledger model), `resolve_xref`, `xref_list`. No module global. |
| `resources/loader.py` | `AngeeResource` (import-export `ModelResource`) owning row hashing, xref, adoption, ledger upsert as **methods**; `build_resource` factory; `result_counts`. |
| `resources/managers.py` | `ResourceQuerySet` + `ResourceManager` (validate/load/diff). Caller passes `addons`; the manager does not reach into discovery (see §2.4). |
| `resources/models.py` | `Resource(AngeeModel)` abstract source model; `Tier = ResourceTier`. |
| `resources/management/commands/angee_resources.py` | `validate`/`load`/`diff` subcommands; discovers addons and passes them in. |

### 1.3 `angee.compose` — build-time (imports `base`; uses `resources` only via discovery)

| Module | Responsibility |
|---|---|
| `compose/discovery.py` | `discover_addons()` — installed `BaseAddonConfig`s in dependency order. |
| `compose/runtime.py` | **`AngeeRuntime`** — the build object owning the whole emit lifecycle (see §2.4). `RuntimePlan` disappears into its state. |
| `compose/rebac.py` | `write_permissions`, `sync_permissions`. |
| `compose/management/commands/angee.py` | `build` (emit + optional check) and `clean` subcommands. `makemigrations`/`migrate` are a **separate** step (see §2.1). |

### 1.4 Layering rules (enforced by a test, §5)

- `base` imports neither `compose` nor `resources`.
- `resources` imports `base`, never `compose`.
- `compose` imports `base`; it touches `resources` only through discovery /
  emission at build time, never on the serving path.

---

## 2. Resolved design decisions

### 2.1 Build is emit-only; the flag is gone

The old `ANGEE_BUILDING`/argv flag existed only because emit and `makemigrations`
ran in **one process**: `import_models()` eagerly loads the previously-emitted
runtime at `django.setup()`, so when emit regenerated those files in the same
process, `makemigrations` diffed against stale, cached modules.

**New design — split the two steps:**

- `AngeeRuntime.emit()` reads source addons' **abstract** models and writes
  `runtime/<label>/models.py`, `runtime/<name>.graphql`, and `permissions.zed`.
  Emission consumes source models only, so whatever the registry holds is
  irrelevant — no flag, no suppression.
- `makemigrations` + `migrate` + `sync_permissions` run as a **separate, later
  step** (a fresh process). That process's `django.setup()` imports the
  just-emitted concrete models normally via the unchanged `import_models()`
  adoption, and the autodetector sees prior migration history for free through
  the normal `call_command`. Header normalization (strip wall-clock timestamps)
  runs after `makemigrations`.

`import_models()` adoption **stays**. There is no isolated registry and no
autodetector reimplementation. `angee dev` (and the `angee` command) sequence:
`build` (emit) → `makemigrations` → `migrate` → serve as distinct steps.

> Decision for the plan-review agents to confirm: the exact command surface —
> whether `angee build` emits-only and a sibling step runs migrations, or `angee
> build` shells a fresh `makemigrations` subprocess after emit. Default: `angee
> build` emits + checks; migrations are a separate documented step the CLI runs.

### 2.2 `Resource` emits the `base` label

`BaseConfig.source_model_modules = ("angee.resources.models",)`. The composer
scans `BaseConfig`'s `models.py` (only `AngeeModel`, excluded) **plus** that extra
module, finds the abstract `Resource`, and emits it into `runtime/base/models.py`
under `app_label = "base"` → `base.Resource`. `MIGRATION_MODULES["base"] =
"runtime.base.migrations"` (already produced by `compose_defaults`). `angee.resources`
itself is a plain app and emits no concrete models.

### 2.3 Drop `.angee-manifest.json`

- `emit()` does **not** write a manifest; delete the manifest writer and the
  `_resource_manifest` emission helper.
- The reset/clean destructive guard no longer keys off the manifest. Re-ground it
  on the explicit `runtime_dir` the host passes plus the generated
  `runtime/__init__.py` (which carries `RUNTIME_APPS`): refuse to delete a
  directory that is not the configured runtime dir / lacks that marker.
- `--check` renders the would-be output to an in-memory `{relative_path: text}`
  map and diffs it against what is on disk — no manifest, no git.
- Remove the manifest from `_generated_source_files` / drift inclusion.

### 2.4 Compose behavior onto classes

- **`AngeeRuntime`** (`compose/runtime.py`) owns the build. Constructors:
  `AngeeRuntime.from_settings(addons=None)` (discovers via `discover_addons`,
  reads `settings.ANGEE_RUNTIME_DIR`/`ANGEE_RUNTIME_MODULE`) and
  `AngeeRuntime.from_addons(addons, *, runtime_dir, runtime_module)`. State:
  addons, extension grouping, runtime labels, runtime_dir. Methods:
  `render_sources() -> dict[str, str]` (deterministic `{relative_path: text}`),
  `emit()` (write the rendered map + permissions + schema SDL),
  `check()` (diff `render_sources()` + rendered SDL against disk; raise on drift),
  `reset()` (authoritative reset preserving migrations, guard per §2.3),
  `clean()`. Field-collision and extension grouping are private methods. The
  emission string-building helpers (`_models_source`, `_class_import`, …) may stay
  module-level **pure** renderers owned by the class, or become private methods —
  decide by cohesion, not layering. `RuntimePlan`, `pipeline.run`, and
  `clean_runtime` disappear into `AngeeRuntime`.
- **`AngeeResource`** (`resources/loader.py`) owns row hashing, xref derivation,
  adoption, and ledger upsert as **methods** (not loose functions that take the
  resource from outside). `build_resource(model, entry, *, ledger_model)` stays a
  thin factory (or becomes `AngeeResource.build(...)`).
- **`ResourceManager`** does not call `discover_addons`. Its methods take `addons`
  (the management command / `AngeeRuntime` discovers and passes them), removing the
  `managers → discovery` edge so all of `resources` imports cleanly at top.
- **GraphQL schemas**: optional small `GraphQLSchemas.from_addons(addons)` owner
  with `names()`, `build(name)`, `render_sdl()`. Lower priority; only if it reads
  cleaner than the current module functions.

### 2.5 Imports at module top

Every import at the top of its module. The **only** permitted deferrals, each
marked with a one-line comment naming the reason:
- Django app-loading order: `base/apps.py` (an `AppConfig` module, loaded in
  populate phase 1 before the registry is ready) defers importing model classes
  and signal wiring until a method that runs after `ready()`. Verified necessary:
  importing `angee.base.models` at apps top raises `AppRegistryNotReady`.
- `TYPE_CHECKING`-only imports stay under the module-top `if TYPE_CHECKING:` block.

No other function-local imports anywhere. The cycles that forced them are removed
structurally (leaf `exceptions.py`/`tiers.py`; `resolve_model` in `entries`;
`ResourceManager` no longer importing discovery; `Resource` not re-exported through
`base.models`).

### 2.6 `adopt` is opt-in

`ResourceEntry.adopt: bool = False`, declared per entry (`{"adopt": true}`).
`AngeeResource` only adopts a pre-existing row (matched by a single unique field)
when `self.entry.adopt`; otherwise a row without a ledger entry is always treated
as new. Default off changes nothing for the existing demo load.

### 2.7 Naming & docstrings

Follow the backend Naming section exactly (role-named modules; `*Config`,
`*Mixin`, `*Manager`, `*QuerySet`, `*Widget` suffixes; verb-first `get_/is_/has_/
as_/to_/from_/create_` methods). Docstrings on every public symbol and manifest
attribute; private helpers where the role is not obvious.

---

## 3. Execution model

1. **Preserve reference, don't copy.** `git mv src/angee/base src/angee/_base_old`.
   Codex reads `_base_old` only to understand behavior/edge-cases; it writes every
   new module fresh from the contracts above + the guidelines. `_base_old` is
   deleted in the final slice. (`_base_old` is not a valid addon — exclude it from
   discovery/imports; nothing new imports it.)
2. **Slice by slice.** Execute the slices in §4 in order. Each slice: write the
   module(s) fresh, decompose into classes/methods per guidelines, then run that
   slice's verification gate before moving on. Commit per slice.
3. **Per-slice gate:** `uv run ruff check .` and `uv run ruff format --check .`
   clean for touched files; `uv run mypy src/` clean; the slice's named tests
   pass. Full suite + example e2e + example build run at the end (Slice 9).
4. **No placeholders, no TODOs.** Each module is complete when its slice closes.

---

## 4. Slices

> Each slice lists scope, target files, the classes/methods/contracts to create,
> decomposition guidance, and its verification gate. Write fresh; do not paste.

### Slice 0 — Scaffold & preserve
- `git mv src/angee/base src/angee/_base_old`.
- Create empty package dirs: `src/angee/base/`, `src/angee/compose/`,
  `src/angee/resources/` with `__init__.py` (and `management/commands/` skeletons
  where needed). Keep `angee` a namespace package (no `src/angee/__init__.py`).
- Gate: `uv run python -c "import angee"` style sanity is N/A yet; just confirm the
  tree exists and `ruff` parses empty packages. Commit.

### Slice 1 — `base/mixins.py` + `base/models.py`
- `mixins.py`: the four mixins, top-level `import reversion`.
- `models.py`: `AngeeModel` (abstract; `get_composition_label`,
  `get_extension_target`, `normalize_model_label`, `get_extension_bases`,
  `public_id` property, `from_public_id(cls) -> Self | None`),
  `instance_from_public_id(model, value)` and `public_id_of(instance)` (the single
  duality helpers — AngeeModel via its contract, plain Django via pk). No
  `Resource` re-export.
- Gate: `tests/test_composition.py` (rewritten in Slice 8) will cover these; for
  now `mypy`/`ruff` clean + `python -c "from angee.base.models import AngeeModel"`
  under Django settings. Commit.

### Slice 2 — `resources` leaf + value modules
- `resources/exceptions.py` (`ResourceLoadError`), `resources/tiers.py`
  (`ResourceTier`), `resources/entries.py` (`ResourceEntry` with `adopt`,
  `ResourceRow`, `ResourceGroup`, `LoadResult`, `ValidationResult`, `resolve_model`,
  format constants — **text formats only**, no binary surface),
  `resources/ordering.py`, `resources/fetch.py`. All imports at top
  (`entries` imports `fetch` and `exceptions` from leaves — no cycle).
- Gate: `mypy`/`ruff` clean. Commit.

### Slice 3 — `resources` import-export core
- `resources/widgets.py` (xref widgets + `XrefWidgetMixin` ledger-model carrier;
  `resolve_xref(value, ledger_model)`; no module global).
- `resources/loader.py` (`AngeeResource` with row-hash/xref/adopt/ledger-upsert as
  methods; `adopt` gated on `entry.adopt`; `build_resource` factory; `result_counts`).
- `resources/managers.py` (`ResourceQuerySet`+`ResourceManager`; methods take
  `addons`, no `discover_addons` import).
- `resources/models.py` (`Resource(AngeeModel)`, `Tier = ResourceTier`).
- `resources/apps.py` (plain `AppConfig`), `resources/management/commands/angee_resources.py`.
- Gate: `resources/tests/` (rewritten Slice 8) cover load/validate/diff/ordering/
  fetch/adopt; `mypy`/`ruff` clean. Commit.

### Slice 4 — `base/apps.py`
- `BaseAddonConfig` (cached_property facts; `source_model_modules`;
  `_model_contributions` scanning `source_models_module` + extra modules with the
  documented deferred `AngeeModel` import; `import_models()` adoption) + `BaseConfig`
  (`source_model_modules = ("angee.resources.models",)`; `ready()` → deferred
  `register_revision_models`).
- Gate: `tests/test_apps.py` (Slice 8); `mypy`/`ruff` clean. Commit.

### Slice 5 — `base/graphql/*`
- `introspection.py`, `crud.py`, `subscriptions.py`, `schema.py` (optionally
  `GraphQLSchemas`), `__init__.py` re-exports.
- Gate: `tests/test_graphql.py`, `tests/test_crud.py`, `tests/test_subscriptions.py`
  (Slice 8). Commit.

### Slice 6 — `base` serving + `base/signals.py`
- `signals.py` (publishers + `register_revision_models`), `views.py`,
  `consumers.py`, `asgi.py`, `urls.py`.
- Gate: import-time + `tests/test_subscriptions.py` for signal wiring. Commit.

### Slice 7 — `base/settings.py`
- `compose_defaults(...)` pure helper: INSTALLED_APPS (incl. `angee.resources`),
  MIDDLEWARE, AUTHENTICATION_BACKENDS, CHANNEL_LAYERS, REBAC_*, MIGRATION_MODULES
  (base + addons → `runtime.<label>.migrations`), no manifest, no `sys.path`
  mutation (host owns that). 
- Gate: `tests/test_settings.py` (Slice 8). Commit.

### Slice 8 — `compose/*` + the build/migrate split
- `compose/discovery.py` (`discover_addons`), `compose/rebac.py`
  (`write_permissions`, `sync_permissions`), `compose/runtime.py` (`AngeeRuntime`
  per §2.4 — emit-only build, `render_sources()` map, `check()` diff, `reset()`/
  `clean()` guard per §2.3, **no manifest, no flag**),
  `compose/management/commands/angee.py` (`build` = emit+check; `clean`; migrations
  are the separate documented step per §2.1).
- Gate: example build (emit) then a separate `makemigrations` run; `tests/
  test_layering.py` (compose isolation), `tests/test_composition.py`. Commit.

### Slice 9 — Tests rewrite, cleanup, full verification
- Rewrite/move all tests to the new structure: `tests/` (settings, apps,
  composition, graphql, crud, subscriptions, layering, settings) and
  `resources/tests/` (resources, resource_features). Update `pyproject.toml`
  `testpaths`/`pythonpath` if paths move. Layering test asserts the §1.4 rules.
- Delete `src/angee/_base_old/`.
- Full gate (all green): `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run mypy src/`, `uv run pytest`, example e2e
  (`--ds=host.settings`), `angee build` (emit) + `makemigrations` + the example
  composer flow. Commit.

---

## 5. Tests

Rewrite (not copy) to cover, at least, what the current suite covers:
`compose_defaults` (base migration redirect + single install), `BaseAddonConfig`
facts (`model_classes` incl. `base.Resource` via `source_model_modules`,
`schema_parts` normalization/rejection), REBAC composition, CRUD/delete-preview,
GraphQL merge + collision, subscriptions gating + publishers (now in `signals`),
resource load/validate/diff/ordering/fetch/xref-collision/adopt-flag, and the
layering rules. Add a test that emit + a separate `makemigrations` produces a
correct (non-stale) migration without any build flag.

---

## 6. Verification & review gates

- Per-slice: ruff (check+format), mypy, that slice's tests.
- Final: full `pytest`, example e2e (`--ds=host.settings`), `angee build` emit +
  separate `makemigrations`/`migrate`, drift `--check`.
- After execution, re-run the three-reviewer pass (Claude subagent + Codex +
  Gemini) against the rewritten tree on the same lenses (Django idiom, classes,
  imports, lifted code) and fix anything they surface.

---

## 7. Pre-execution workflow (before Codex runs)

1. **Review the plan + guidelines with three agents.** Dispatch Claude subagent,
   Codex, Gemini to (a) stress-test this plan for gaps/risks (esp. §2.1 build split,
   §2.2 base-label emission, §2.5 import exceptions, the slice ordering) and (b)
   check whether `docs/*guidelines*` fully and unambiguously specify the rules an
   executor must follow (compose-onto-classes, imports-at-top + Django exception,
   naming, docstrings, "rewrite don't copy").
2. **Improve guidelines & docs** from that review so the executor needs only the
   guidelines + this plan — no tribal knowledge.
3. **Then** let Codex execute slice by slice.

---

## 8. Self-review checklist (run before handing to the review agents)

- [ ] Every old module maps to a new home (apps, mixins, models, signals, graphql/*,
      views, consumers, asgi, urls, settings → base; discovery, runtime, rebac,
      angee cmd → compose; exceptions, tiers, entries, ordering, fetch, widgets,
      loader, managers, models, angee_resources cmd → resources). No module lost.
- [ ] The flag (`ANGEE_BUILDING`/argv/`_running_angee_build`) appears **nowhere** in
      the target; the build is emit-only and migrations are a separate step.
- [ ] `.angee-manifest.json` is written nowhere; reset/clean guard re-grounded;
      `--check` is emit-and-diff.
- [ ] `base.Resource` (not `resources.Resource`) is the emitted label.
- [ ] No function-local imports except the documented Django-phase-1 deferrals in
      `base/apps.py` and `TYPE_CHECKING` blocks.
- [ ] Layering: `base` imports neither `compose` nor `resources`.
- [ ] Each slice has a concrete verification gate; final gate covers ruff/mypy/
      pytest/e2e/build.
