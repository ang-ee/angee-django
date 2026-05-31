# Compose / Base / Resources Refactor — Implementation Plan

> **For the executing agent (Codex).** This is a clean **rewrite**, not a port.
> Do **NOT** copy or mechanically translate the old code. For each module, read the
> *contract* here and the guidelines (`AGENTS.md`, `docs/guidelines.md`,
> `docs/backend/guidelines.md` — especially **Package Layering**, compose-onto-classes,
> imports-at-top, naming, docstrings — `docs/stack.md`, `docs/glossary.md`), then
> write the module fresh, decomposing behavior into classes and methods per the
> guidelines. The pre-refactor tree is preserved at `.agents/reference/base_old/`
> as a **behavioral reference only** (what it does and the edge cases it handles) —
> never as a source to paste; it is deleted in the final slice. Execute **slice by
> slice**, running each slice's verification gate before the next, and commit per
> slice.

**Goal:** Restructure the framework core from one `angee.base` package into three
clean packages — `angee.compose` (build-time), `angee.base` (runtime), and
`angee.resources` (resource subsystem) — dropping `.angee-manifest.json` and the
"is a build running" flag, composing behavior onto classes, and putting every
import at module top, rewritten to the guidelines.

**Architecture:** Three layers, one-way dependency (a test enforces it):
`angee.base` is pure runtime and imports neither sibling; `angee.resources` and
`angee.compose` may import `angee.base`; `angee.compose` reads resource/addon
declarations at build time but no serving module imports it. The build is **emit
only** (Stage 1); `makemigrations`/SDL/`migrate`/sync/resource-load are separate
later steps (Stage 2) run in a fresh process; the Go `angee` CLI orchestrates the
stages out of band. `Resource` is emitted under the `base` app label via the
`source_model_modules` seam, keeping `base.Resource`.

**Tech stack:** Python 3.14, Django 6, strawberry-django, channels/daphne,
django-zed-rebac, django-import-export, django-reversion, django-simple-history,
django-sqids, tablib, pytest-django, ruff, mypy, uv.

---

## 1. Target architecture

Three sibling packages under the `angee` namespace package (no
`src/angee/__init__.py`). Each package: docstring-only `__init__.py` (except the
`base/graphql` re-export facade, which keeps `__all__`) and a `py.typed` marker.
Docstrings on every public module/class/method/function, declarative manifest
attribute, and public module-level constant; private helpers when their role is
not obvious from name + signature.

### 1.1 `angee.base` — runtime (imports neither `compose` nor `resources`)

| Module | Responsibility |
|---|---|
| `base/apps.py` | `BaseAddonConfig` (addon contract) + `BaseConfig`. Facts as `cached_property`: `model_classes`, `model_extensions`, `schema_parts`, `rebac_schema_path`, `resource_manifest`, `dependencies`, `source_models_module`, `graphql_module`. Declares `source_model_modules`, `depends_on`, `rebac_schema`, `resources`. `_model_contributions` scans `source_models_module` **plus** each `source_model_modules` entry; classes from an explicitly listed module are owned by this config **regardless of package prefix** (do not filter them out by `_belongs_to_source_module`). `import_models()` adoption stays. `BaseConfig` (label `base`): `source_model_modules = ("angee.resources.models",)`; `ready()` calls `register_revision_models` via a deferred import (Django phase-1). |
| `base/discovery.py` | `discover_addons()` — installed `BaseAddonConfig`s in dependency order. **Lives in `base`** because serving code (schema building, ASGI routing) enumerates addons; `compose` and the `resources` command import it from here. |
| `base/mixins.py` | `TimestampMixin`, `SqidMixin`, `HistoryMixin`, `RevisionMixin`. |
| `base/models.py` | `AngeeModel` (abstract base; composition/extension classmethods, `public_id` property, `from_public_id -> Self | None`); `instance_from_public_id`, `public_id_of` (the single AngeeModel-or-plain-Django duality helpers). |
| `base/deletion.py` | `DeletionPreview` / `DeletionPreviewGroup` domain objects + `DeletionPreview.from_instance(instance)` owning Django's `Collector` cascade forecast. (Moved out of graphql.) |
| `base/signals.py` | Change publishers (`connect_publishers`, `_on_save`, `_on_delete`, `_publish`, `_broadcast`, `change_group`, `_json_safe`) + `register_revision_models`. `_publish` uses `public_id_of(instance)`. |
| `base/access.py` | `ChangeReadGate(model, actor)` — REBAC read-gate + field redaction for change events (`filter(payload) -> ChangeEvent | None`), resolving `model_resource_type`/`gated_read_fields` once. (Authorization moved out of graphql; could instead be owned by `django-zed-rebac` if it exposes the hook.) |
| `base/graphql/__init__.py` | Re-export facade (`__all__` allowed): `crud`, `changes`, `ChangeEvent`, `GraphQLSchemas`, `DEFAULT_SCHEMA_NAME`. No `render_sdl`. |
| `base/graphql/introspection.py` | `surface_name`, `surface_field_names`, `django_model` — the **only** reader of Strawberry internals. |
| `base/graphql/schema.py` | `GraphQLSchemas` owner: `from_discovery()` / `from_addons(addons)`, cached `parts`, `names()`, `build(name) -> strawberry.Schema`; private `_merge_root`/`_dedupe_by_identity`. Live schema only — no SDL files. |
| `base/graphql/events.py` | `ChangeEvent` Strawberry type (+ `from_payload`). |
| `base/graphql/crud.py` | `crud(...)` factory + the Strawberry `DeletePreview`/`DeletePreviewGroup` output types + delete resolver that adapts a `base.deletion.DeletionPreview`. No `Collector` logic. |
| `base/graphql/subscriptions.py` | `changes(model, field=...)` thin factory (wires `connect_publishers`) + the resolver (reads `actor` from `info.context`, drains the channel stream, applies `ChangeReadGate`). The channel-drain stream stays a stateless `_subscribe` generator here. |
| `base/views.py` | `graphql_endpoint` + cached `_get_view`. |
| `base/consumers.py` | `AngeeGraphQLWSConsumer` + `scope_actor` (moved here; the WS transport owns scope→context). |
| `base/asgi.py` | `build_application()` routing only (uses `GraphQLSchemas.names()`/`build`). |
| `base/urls.py` | `urlpatterns` only. |
| `base/settings.py` | `compose_defaults(...)` — pure host settings helper producing the build vs run app sets (see §2.1). No manifest, no `sys.path` mutation. |

### 1.2 `angee.resources` — resource subsystem (imports `base`, never `compose`)

A plain Django app (has an `AppConfig` so its `resources` command is discoverable;
it is **not** a `BaseAddonConfig`, so `discover_addons` ignores it). Its abstract
`Resource` source model is pulled into the `base` label via
`BaseConfig.source_model_modules`, so the emitted concrete model is `base.Resource`.

| Module | Responsibility |
|---|---|
| `resources/apps.py` | Plain `AppConfig` (`name = "angee.resources"`). |
| `resources/exceptions.py` | `ResourceLoadError` (leaf). |
| `resources/tiers.py` | `ResourceTier` TextChoices + `from_value` (leaf). |
| `resources/entries.py` | `ResourceEntry` (with `adopt: bool = False`), `ResourceRow`, `ResourceGroup`, `LoadResult`, `ValidationResult`, `resolve_model`, text-format constants (csv/tsv/json/yaml only — no binary). |
| `resources/ordering.py` | `order_entries` (depends_on topo-sort). |
| `resources/fetch.py` | `fetch_url` (http/https cache). |
| `resources/widgets.py` | `XrefForeignKeyWidget`/`XrefManyToManyWidget` + `XrefWidgetMixin` (ledger-model carrier), `resolve_xref(value, ledger_model)`, `xref_list`. No module global. |
| `resources/loader.py` | `AngeeResource` (import-export `ModelResource`) owning row-hash / xref / adopt / ledger-upsert as **methods**; adoption gated on `self.entry.adopt`; `build_resource(model, entry, *, ledger_model)` factory; `result_counts`. |
| `resources/managers.py` | `ResourceQuerySet` + `ResourceManager` (validate/load/diff). Methods **take `addons`** (caller discovers + passes); no `discover_addons` import. |
| `resources/models.py` | `Resource(AngeeModel)` abstract source model; `Tier = ResourceTier`. |
| `resources/management/commands/resources.py` | `resources load|validate|diff` — discovers addons (`base.discovery`) and passes them in. |

### 1.3 `angee.compose` — build-time (imports `base`; reads `resources` only as build input)

| Module | Responsibility |
|---|---|
| `compose/apps.py` | Plain `ComposeConfig(AppConfig)` (`name = "angee.compose"`) so the `angee` command is discoverable; not a `BaseAddonConfig`. |
| `compose/runtime.py` | **`AngeeRuntime`** — owns the build (see §2.4): `from_settings()`/`from_addons()`, `render_sources()`, `emit()`, `check()`, `reset()`, `clean()`, and the Stage-2 SDL methods `render_schema_sdl()`/`write_schema_sdl()`/`check_schema_sdl()` (import `base.graphql.GraphQLSchemas`). `RuntimePlan` disappears into its state. |
| `compose/rebac.py` | `write_permissions`, `sync_permissions`. |
| `compose/management/commands/angee.py` | `build` (emit + `--check`) and `clean` subcommands; an `schema` subcommand for SDL emit/check (run settings). Migrations are Django-native (separate). |

### 1.4 Layering (a test enforces)

- `base` imports neither `compose` nor `resources`.
- `resources` imports `base`, never `compose`.
- `compose` imports `base`; no serving module (`asgi`, `urls`, `views`, `consumers`,
  `signals`, `models`, `graphql/*`) imports `compose`.
- Addon discovery is a `base`-level registry read shared by both upper layers.

---

## 2. Design decisions

### 2.1 Build stages, atomic commands, build/run app sets — and why there is no flag

The old `ANGEE_BUILDING`/argv flag existed only because emit and `makemigrations`
ran in one process: `import_models()` eagerly loads the previously-emitted runtime
at `django.setup()`, so regenerating in the same process left `makemigrations`
diffing stale modules. The fix is to separate the stages and let build and run use
different `INSTALLED_APPS`. `import_models()` adoption **stays**.

- **Build app set** (from `compose_defaults`): source addons + `angee.compose`
  (command host). No runtime serving apps.
- **Run app set**: `angee.base` + `angee.resources` + source addons (with
  `import_models()` adoption).

Stages — each an **atomic, single-purpose** management command, safe in its own
process. The **Go `angee` CLI / `angee dev` orchestrates** the order out of band;
no Python command runs the whole pipeline.

- **Stage 1 — Compose (the real Django build).** `manage.py angee build` under
  build settings: `AngeeRuntime.emit()` writes `runtime/<label>/models.py`,
  `runtime/__init__.py` (generated sentinel + `RUNTIME_APPS`), and the combined
  `permissions.zed`, all from the **abstract** source models. No runtime load, no
  SDL, no migrations. `angee build --check` is a pure in-memory `{path: text}` diff
  of rendered model sources + permissions vs disk. `angee clean` per §2.3.
- **Stage 2 — Post-build runtime steps**, run under run settings in a fresh process
  that loads the emitted concrete models normally, in dependency order:
  `makemigrations <all labels>` (one call, + header normalization) → `migrate` →
  `manage.py angee schema` (emit/check `runtime/schemas/*.graphql`; needs concrete
  models loaded) → permission sync (one owner — see §2.4) → `manage.py resources
  load` (data; needs DB, strictly after `migrate`).
- **Stage 3 — Frontend build** consumes the emitted SDL (out of scope here).

Prefer Django-native commands (`makemigrations`, `migrate`) where they exist; add
an `angee`/`resources` subcommand only where Django has none. `compose_defaults`
stays pure: the host resolves `ANGEE_RUNTIME_DIR`/`ANGEE_DATA_DIR` and puts the
runtime parent on `sys.path`; the CLI selects build vs run settings per invocation.

### 2.2 `Resource` emits the `base` label

`BaseConfig.source_model_modules = ("angee.resources.models",)`. `_model_contributions`
scans `BaseConfig.models_module` (only `AngeeModel`, excluded) plus that module, and
**owns** the abstract `Resource` it finds there even though its dotted path is
outside `angee.base` (the package-prefix filter must not drop explicitly listed
modules). It emits into `runtime/base/models.py` as `app_label = "base"` →
`base.Resource`. `compose_defaults` sets `MIGRATION_MODULES["base"] =
"runtime.base.migrations"`. `angee.resources` itself emits no concrete models.
Refer to the concrete model via `apps.get_model("base", "Resource")`, never by
importing `runtime/`.

### 2.3 Drop `.angee-manifest.json`

- `emit()` writes no manifest; there is no `_resource_manifest` emission helper.
- `runtime/__init__.py` carries a generated **sentinel** line plus `RUNTIME_APPS`.
- `reset()`/`clean()` refuse to delete unless `path == resolved ANGEE_RUNTIME_DIR`
  **and** (the dir is empty **or** `runtime/__init__.py` carries the sentinel).
  Parse the previous `RUNTIME_APPS` by reading text, never by importing generated
  code. Preserve `*/migrations/`; delete only known generated file classes. A first
  build into an empty configured dir is allowed.
- `--check`: model-source drift = pure `{path: text}` diff (Stage 1); SDL drift =
  import-then-render-then-diff in the run-settings `angee schema` step (Stage 2).

### 2.4 Compose behavior onto classes

- **`AngeeRuntime`** (`compose/runtime.py`) owns the whole build lifecycle (§2.1
  Stage 1 + the Stage-2 SDL methods). `RuntimePlan` becomes its private state. The
  pure string-building renderers may stay module-level functions in `runtime.py`
  (a pure renderer that returns text may stay a function) or be private methods —
  decide by cohesion.
- **`GraphQLSchemas`** (`base/graphql/schema.py`) folds addon parts once and owns
  `names()`/`build(name)`; replaces the loose `collect_*`/`build_schema`/`render_sdl`
  functions and the double discover/fold ASGI does today.
- **`AngeeResource`** (`resources/loader.py`) owns row-hash / xref / adopt /
  ledger-upsert as methods.
- **`ResourceManager`** methods take `addons` (no `discover_addons` import), so all
  of `resources` imports cleanly at top.
- **`DeletionPreview`** (`base/deletion.py`) owns the `Collector` cascade forecast;
  `crud` only serializes it.
- **`ChangeReadGate`** (`base/access.py`) owns change-event read authorization.
- **One permission-sync owner:** pick `compose.sync_permissions` (wrapping the
  library) **or** the library `rebac sync` directly, and document the choice; do
  not keep two competing sync paths.

### 2.5 GraphQL placement (build vs runtime)

`base.graphql` owns only the **live** schema + the Strawberry surface. SDL file
rendering/writing/checking is **build-time** and lives in `compose` (the Stage-2
`AngeeRuntime` SDL methods), importing `base.graphql.GraphQLSchemas` (`compose →
base`, allowed). `subscriptions.py` is split: `ChangeEvent` → `events.py`;
authorization (`ChangeReadGate`) → `base/access.py` (not graphql); `scope_actor` →
`consumers.py`; leaving a thin `changes()` factory + resolver + stream.
`introspection.py` stays the sole Strawberry-internal reader. `schema_parts`
normalization stays on `BaseAddonConfig` (AppConfig owns addon-local facts).

### 2.6 Imports at module top

Every import at the top. The only permitted deferrals, each marked with a one-line
reason comment: (a) Django app-loading order — `base/apps.py` defers importing model
classes and signal wiring until a method that runs after `ready()` (verified:
importing `angee.base.models` at apps top raises `AppRegistryNotReady`); (b)
`TYPE_CHECKING` blocks. Probe optional/generated modules with
`importlib.util.find_spec` (verifying parents), never `try/except ImportError`.

### 2.7 `adopt` is opt-in

`ResourceEntry.adopt: bool = False`, declared per entry (`{"adopt": true}`).
`AngeeResource` adopts a pre-existing row (single unique-field match) only when
`self.entry.adopt`; otherwise a row without a ledger entry is always new. Default
off is behavior-neutral for the existing demo load.

### 2.8 Naming & docstrings

Follow the backend Naming section exactly (role-named modules; `*Config`/`*Mixin`/
`*Manager`/`*QuerySet`/`*Widget` suffixes; verb-first `get_/is_/has_/as_/to_/from_/
create_` methods). Docstrings per §1 intro.

---

## 3. Execution model

1. **Preserve reference outside the package.** `git mv src/angee/base
   .agents/reference/base_old` (NOT under `src/angee`, so it is not linted, typed,
   packaged, or importable). Delete stale `__pycache__` and any committed
   `.angee-manifest.json` in example runtime output. Codex reads `base_old` only to
   understand behavior; it writes every new module fresh. Delete `base_old` in the
   final slice.
2. **Slice by slice** (§4), in order. Each slice: write the module(s) fresh,
   decompose per guidelines, put that behavior's tests in the same slice, run the
   gate, commit.
3. **Per-slice gate:** `ruff check`/`ruff format --check` clean; `mypy src/` clean
   (clean throughout, since `base_old` is outside `src`); that slice's tests pass.
   A minimal `base/__init__.py` + `base/apps.py` scaffold must exist before any
   slice triggers `django.setup()`.
4. **No placeholders/TODOs.** A module is complete when its slice closes.

---

## 4. Slices

### Slice 0 — Scaffold & preserve
`git mv src/angee/base .agents/reference/base_old`; delete stale `__pycache__` and
example `.angee-manifest.json`. Create `base/`, `compose/`, `resources/` package
dirs with docstring-only `__init__.py` + `py.typed` + `management/commands/`
skeletons where needed. Update `pyproject.toml` `testpaths`/`pythonpath` for the new
`resources/tests/`. Gate: tree parses; ruff clean. Commit.

### Slice 1 — `base/mixins.py` + `base/models.py` (+ `base/__init__.py`)
Per §1.1. Tests: `tests/test_composition.py` (AngeeModel/REBAC mixin), id-helper
duality. Gate: those tests + mypy/ruff. Commit.

### Slice 2 — `resources` leaves & value modules
`exceptions.py`, `tiers.py`, `entries.py` (incl. `adopt`, text formats only,
`resolve_model`), `ordering.py`, `fetch.py`. Tests: ordering, fetch (scheme reject,
cache), entry value behavior, model conflict. Gate + commit.

### Slice 3 — `resources` import-export core
`widgets.py`, `loader.py` (`AngeeResource` methods + adopt-gating), `managers.py`
(addons-passed), `models.py` (`Resource`, `Tier`), `apps.py`,
`management/commands/resources.py`. Tests: load/validate/diff, xref collision,
adopt-flag on/off. Gate + commit.

### Slice 4 — `base/apps.py` + `base/discovery.py`
`BaseAddonConfig` (cached_property facts; `source_model_modules` ownership fix;
deferred model imports) + `BaseConfig`; `discover_addons`. Tests: `tests/test_apps.py`
(`schema_parts` normalize/reject; `model_classes` includes `base.Resource` via
`source_model_modules`), discovery order/cycle. Gate + commit.

### Slice 5 — `base/deletion.py`, `base/signals.py`, `base/access.py`
`DeletionPreview.from_instance` (Collector); change publishers (+ `public_id_of`) +
`register_revision_models`; `ChangeReadGate`. Tests: deletion preview, signal
publish/`change_group`, gate filter/redact. Gate + commit.

### Slice 6 — `base/graphql/*`
`introspection.py`, `schema.py` (`GraphQLSchemas`), `crud.py` (factory + delete
resolver adapting `base.deletion`), `events.py`, `subscriptions.py`, `__init__.py`
facade. Tests: `tests/test_graphql.py` (merge + collision), `tests/test_crud.py`
(delete-preview), `tests/test_subscriptions.py` (changes + gating via `ChangeReadGate`).
Gate + commit.

### Slice 7 — `base` serving + `base/settings.py`
`views.py`, `consumers.py` (+ `scope_actor`), `asgi.py`, `urls.py`;
`compose_defaults` (build + run app sets; MIGRATION_MODULES incl. base). Tests:
`tests/test_settings.py` (base migration redirect; build set installs
`angee.compose`; run set installs `angee.resources`; single install of each).
Gate + commit.

### Slice 8 — `angee.compose`
`apps.py` (`ComposeConfig`), `discovery` already in base, `rebac.py`
(`write_permissions`/`sync_permissions`), `runtime.py` (`AngeeRuntime`: emit-only
build, `render_sources()` map, `--check` diff, reset/clean guard per §2.3, Stage-2
SDL methods importing `GraphQLSchemas`), `management/commands/angee.py`
(`build`/`clean`/`schema`). Tests: `tests/test_layering.py` (the §1.4 rules incl.
command packages), `tests/test_composition.py` (emit + separate `makemigrations`
yields a non-stale migration with no flag; first build from no runtime; stale-runtime
`--check`; SDL render only after fresh run-settings load). Gate: example build (emit)
+ separate `makemigrations`. Commit.

### Slice 9 — cleanup & full verification
Update every test/example import (`angee.base.resources.*`/`angee.base.compose.*`),
the `get_commands()["resources"] == "angee.resources"` assertion, and the dev
template (`templates/stacks/dev/.../angee.yaml.jinja`) to invoke the atomic commands
in stage order (defer `--watch`/asset handling — CLI/template concerns). Delete
`.agents/reference/base_old/`. Full gate (all green): `ruff check .`,
`ruff format --check .`, `mypy src/`, `pytest`, example e2e (`--ds=host.settings`),
the full example stage sequence (`angee build` → `makemigrations` → `migrate` →
`angee schema` → permission sync → `resources load`). Commit.

---

## 5. Tests

Rewrite (not copy) to cover at least the current suite plus the new behavior:
`compose_defaults` (base migration redirect; build set installs `angee.compose`; run
set installs `angee.resources`; single install of each); `BaseAddonConfig` facts
(`model_classes` includes `base.Resource` via `source_model_modules`; `schema_parts`
normalize/reject); discovery order/cycle; REBAC composition; `crud` delete-preview;
`GraphQLSchemas` merge + collision; subscriptions gating via `ChangeReadGate` +
publishers in `signals`; resource load/validate/diff/ordering/fetch/xref-collision
and the `adopt` flag on/off; the §1.4 layering rules (static imports **and** that
`base` does not import `compose`/`resources`); and the build tests in Slice 8
(emit+separate-makemigrations non-stale; first build; stale `--check`; SDL after
fresh load).

## 6. Verification & review gates

Per slice: ruff (check+format), mypy, the slice's tests. Final: full `pytest`,
example e2e, the full stage sequence, drift `--check`. After execution, re-run the
three-reviewer pass over the rewritten tree (Django idiom, classes, imports, lifted
code, layering) and fix what it surfaces.

## 7. Self-review checklist

- [ ] Every old module maps to a new home; nothing lost (apps, discovery, mixins,
      models, deletion, signals, access, graphql/*, views, consumers, asgi, urls,
      settings → base; runtime, rebac, angee cmd → compose; exceptions, tiers,
      entries, ordering, fetch, widgets, loader, managers, models, resources cmd →
      resources).
- [ ] No `ANGEE_BUILDING`/argv/`_running_angee_build` anywhere; build is emit-only;
      migrations/SDL/load are separate Stage-2 steps.
- [ ] `.angee-manifest.json` written nowhere; reset/clean guard per §2.3; `--check`
      is emit-and-diff (sources) + import-render-diff (SDL).
- [ ] `base.Resource` is the emitted label; `source_model_modules` ownership bypasses
      the prefix filter; referenced via `apps.get_model`.
- [ ] `render_sdl` lives in `compose`, not `base.graphql`; `subscriptions.py` split
      (events/access/consumers); `crud` delete-preview in `base.deletion`.
- [ ] No function-local imports except documented Django-phase-1 deferrals + `TYPE_CHECKING`.
- [ ] Layering: `base` imports neither sibling; `discover_addons` in `base`.
- [ ] `angee` command host (`angee.compose`) and `resources` command host
      (`angee.resources`) are installed in the right app sets and excluded from
      addon discovery.
- [ ] `py.typed` in each package; `__init__` docstring-only except the graphql facade.
- [ ] Each slice has a concrete gate; final gate covers ruff/mypy/pytest/e2e + the
      full stage sequence.
