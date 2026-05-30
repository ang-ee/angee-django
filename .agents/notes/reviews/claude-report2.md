# Review 2 — class composition, imports, lifted code, manifest (`src/angee/base/`)

## Summary

The biggest remaining structural issue is Lens A: `compose/emission.py` plus
`compose/pipeline.py` are a textbook "missing class". A passive `RuntimePlan`
dataclass holds three fields while ~14 module functions thread that same plan
(and `runtime_dir`) through every step — exactly the shape the new guideline
forbids ("a dataclass that only holds fields while a sibling module mutates and
emits from it is a missing class"). The runtime build owns a cohesive
plan/emit/check/reset/import lifecycle and should be one `AngeeRuntime` object.
Lens B is the second-order cause: the only true import cycle in the tree is
`models.py` ↔ `resources/models.py` (a bottom-of-file re-export), and it forces
two deferred imports inside `apps.py`; the rest of the deferred imports are
either trivially fixable or hide the same `apps`↔`models`/`loader` seam.
Lens C surfaces real dead weight — `iter_permission_paths` has zero callers, and
several lifted resource/mixin options are speculative. Lens D (drop
`.angee-manifest.json`) is clean to execute: `--check` already works purely by
emit-to-string-and-diff, and the reset/clean safety guard can be re-grounded on
the host-supplied `ANGEE_RUNTIME_DIR` plus the existing `RUNTIME_APPS` marker in
`__init__.py`, so no manifest is needed.

## Findings

### 1. `RuntimePlan` + emission/pipeline module functions are a missing class
- **Lens**: A
- **Location**: `src/angee/base/compose/emission.py:25-181`, `src/angee/base/compose/pipeline.py:44-100`
- **Severity**: High
- **Problem**: `RuntimePlan` (`emission.py:25`) holds only `addons`,
  `extensions`, `labels`. Every public function then takes that plan plus the
  `runtime_dir` as loose parameters: `check_runtime(runtime_dir, plan)`,
  `reset_runtime_dir(runtime_dir, plan)`, `emit_runtime_sources(runtime_dir,
  plan)`, `import_runtime_models(plan)`, `normalize_migration_headers(runtime_dir,
  plan)`, `emit_schema_sdl(runtime_dir, plan)`, `check_schema_sdl(runtime_dir,
  plan)` (`emission.py:51,78,97,121,130,144,157`). `pipeline.run`
  (`pipeline.py:44`) and `clean_runtime` (`pipeline.py:90`) re-derive
  `runtime_dir = Path(settings.ANGEE_RUNTIME_DIR)` and then call the same
  functions in sequence. This is the exact smell the guideline names: functions
  that all read/transform/emit from one object that should own them. The plan and
  the runtime directory are one cohesive build target.
- **Recommendation**: Collapse to one `AngeeRuntime` class that owns `addons`,
  `extensions`, `labels`, and `runtime_dir`, with a classmethod factory
  `AngeeRuntime.plan(addons, runtime_dir)` doing what `plan_runtime` +
  `_check_field_collisions` do today. The free functions become methods:
  `.emit_sources()`, `.check_sources()`, `.import_models()`,
  `.normalize_migration_headers()`, `.emit_schema_sdl()`, `.check_schema_sdl()`,
  `.reset()`, `.clean()`. `pipeline.run` shrinks to a thin orchestrator that
  builds the runtime and calls methods in order; `RuntimePlan` is deleted (it is
  subsumed by the class). The private rendering helpers (`_models_source`,
  `_class_import`, etc.) can stay module-private — they are stateless string
  builders the methods call — but the lifecycle entry points must move onto the
  class. See the proposed shape below.

### 2. `models.py` ↔ `resources/models.py` re-export cycle forces deferred imports
- **Lens**: B
- **Location**: `src/angee/base/models.py:121-133`; `src/angee/base/apps.py:118,240,375`
- **Severity**: High
- **Problem**: `models.py` imports `Resource` from `resources/models.py` at the
  *bottom* of the file with a `# noqa: E402` and a comment explaining the
  ordering hazard (`models.py:126`). `resources/models.py:8` imports `AngeeModel`
  from `models.py` at the top. That is a genuine cycle, only survivable because
  `models.py` defines `AngeeModel` before the trailing import runs. The cycle is
  why `apps.py` cannot import `AngeeModel`/`Resource` at module top and instead
  does `from angee.base.models import AngeeModel` twice inside methods
  (`apps.py:240`, `apps.py:375`) and `from angee.base.resources.models import
  Resource` inside `resource_manifest` (`apps.py:118`). The guideline: "A
  function-local or deferred import is a smell that a module boundary is wrong …
  fix the seam … instead of hiding the import."
- **Recommendation**: The re-export exists only so the composer discovers
  `Resource` through the base addon's conventional `models.py` (the comment says
  so). Break the cycle by making `angee/base/models.py` the single owner of
  `AngeeModel` and *importing* `Resource` is the only coupling — invert it: keep
  `Resource` defined in `resources/models.py`, and re-export it via a clean
  top-level import that does not create a cycle by having `resources/models.py`
  import `AngeeModel` from a leaf module. Concretely: `AngeeModel` depends on
  nothing in `resources/`, so the cycle is purely the trailing re-export.
  Move the re-export to the top of `models.py` is impossible while
  `resources/models.py` imports from `models.py`. The clean fix is to put the
  shared base (`AngeeModel` and the `instance_from_public_id`/`public_id_of`
  logic — see Finding 5) where both can import it without a back-edge, then the
  re-export in `models.py` becomes an ordinary top import and the three
  `apps.py` deferred imports move to module top. If the re-export cannot be made
  cycle-free, it is the one defensible deferred import in the tree and should
  carry that justification — but the `apps.py` locals are not defensible and must
  move to top once the seam is fixed.

### 3. `iter_permission_paths` is dead code
- **Lens**: C
- **Location**: `src/angee/base/compose/rebac.py:13-23`
- **Severity**: Medium
- **Problem**: `iter_permission_paths` has zero callers in `src/`, `tests/`, or
  `examples/` (verified by grep). `write_permissions` (`rebac.py:26`) computes
  per-addon paths itself; nothing consumes `iter_permission_paths`. "Prefer
  deletion to abstraction."
- **Recommendation**: Delete the function. If a future consumer needs ordered
  permission paths, it can ask `addon.rebac_schema_path` directly.

### 4. `apps.py:_running_angee_build` argv fallback is speculative defensiveness
- **Lens**: C
- **Location**: `src/angee/base/apps.py:41-60`
- **Severity**: Medium
- **Problem**: The function checks `BUILDING_ENV_VAR` and *also* a cold-start
  `sys.argv` fallback (`argv[0] == "angee" and argv[1] == "build"`). The
  docstring concedes the env var is the real mechanism and the argv check is a
  "cold-start fallback for a bare `manage.py angee build` where nothing set the
  variable yet." No code path in `examples/` invokes `manage.py angee build`
  without the launcher; `angee dev` is the only supported entry (per AGENTS.md
  "Run From The Root"). This is dead defensiveness branching on `sys.argv` from
  inside framework code — the smell of a function inspecting global state to
  decide behavior.
- **Recommendation**: Drop the argv branch; require the launcher (or any
  programmatic build caller) to set `ANGEE_BUILDING=1` before `django.setup()`.
  If a bare `manage.py angee build` must keep working, the management command can
  set the env var as its first action rather than `import_models()` sniffing
  argv. This removes the `sys` import dependency for this concern.

### 5. `instance_from_public_id` / `public_id_of` are loose functions that should be polymorphic
- **Lens**: A
- **Location**: `src/angee/base/models.py:92-118`
- **Severity**: Medium
- **Problem**: Both functions take a model/instance and `if issubclass(...)` /
  `if isinstance(...)` branch on whether it is an `AngeeModel` vs a plain Django
  model, then fall back to `_default_manager.filter(pk=...)` / `str(pk)`. This is
  the canonical "function that switches on a value's type wants polymorphism"
  smell from the Constitution, and the backend guideline's "give objects
  classmethod factories … rather than re-decoding model shape." `AngeeModel`
  already has `from_public_id` / `public_id` (`models.py:79-89`); the helpers
  exist solely to handle the non-Angee `auth.User` case.
- **Recommendation**: This is borderline — the helpers genuinely bridge to plain
  Django models the framework does not own, so a pure polymorphic move is not
  available (you cannot add a method to `auth.User`). The honest fix is to keep a
  single owner but state the contract once: the resource layer is the only caller
  (`loader.py:20,191,206`, `widgets.py:12,119`, `crud.py:23,152`), and it always
  deals with arbitrary target models, so these belong as small module functions
  in the resource layer's identity seam rather than in `models.py` where they
  add a non-Angee branch to the base model module. If kept in `models.py`, they
  are acceptable as the documented bridge; do not "fix" them into the model class
  since the fallback target is foreign. Flagged for the architect to confirm
  placement — but they should not move into `AngeeModel` methods.

### 6. `RevisionMixin.revisions` does a function-local `import reversion`
- **Lens**: B
- **Location**: `src/angee/base/mixins.py:88`
- **Severity**: Low
- **Problem**: `reversion` is imported inside the `revisions` property. It is not
  an optional dependency — `reversion` is in `INSTALLED_APPS`
  (`settings.py:51`) and `signals.py:31` also imports it (also deferred). The
  guideline allows a local import only for a "genuinely optional at runtime"
  dependency; this is not optional.
- **Recommendation**: Move `import reversion` to the top of `mixins.py`. Same for
  `signals.py:31` (see Finding 7).

### 7. `signals.register_revision_models` deferred imports of `reversion` and `apps`
- **Lens**: B
- **Location**: `src/angee/base/signals.py:31-32`
- **Severity**: Low
- **Problem**: `import reversion` and `from django.apps import apps` are inside
  the function. Neither is optional. `register_revision_models` runs from
  `BaseConfig.ready()` (`apps.py:395`), well after app population, so a top-level
  `from django.apps import apps` is safe. The deferred imports are
  cargo-culted, not load-bearing.
- **Recommendation**: Move both to module top.

### 8. `BaseConfig.ready` deferred import of `register_revision_models`
- **Lens**: B
- **Location**: `src/angee/base/apps.py:395`
- **Severity**: Low
- **Problem**: `from angee.base.signals import register_revision_models` is inside
  `ready()`. `signals.py` imports only Django/channels/asgiref at module top
  (after Finding 7's fix it also imports `reversion` and `django.apps.apps`),
  none of which create a cycle back to `apps.py`. This is a precautionary deferred
  import with no cycle to justify it.
- **Recommendation**: Move to the top of `apps.py`. (Confirm no
  `signals` → `apps` edge after Finding 7; there is none today.)

### 9. `resources/managers.py` defers `loader` and `discovery` imports
- **Lens**: B
- **Location**: `src/angee/base/resources/managers.py:40,68,170`
- **Severity**: Medium
- **Problem**: `ResourceQuerySet.validate_addons` and `load_addons` defer
  `from angee.base.resources.loader import …`, and `_entries_for` defers
  `from angee.base.discovery import discover_addons`. The `loader` import is a
  real cycle: `loader.py:21` imports from `entries.py`, `entries.py` is imported
  by `managers.py:14` at top, and `loader.py` does not import `managers.py` —
  but `managers.py` defining `ResourceManager = Manager.from_queryset` is read by
  `resources/models.py:9`, which is read by `models.py`, which `loader.py:20`
  imports. So `managers → loader → models → resources.models → managers` is a
  cycle through the Finding 2 re-export. `discover_addons` (`discovery.py`)
  imports `apps.py`, which imports `models`/`resources.models`, closing another
  loop. Both deferred imports are symptoms of the Finding 2 cycle.
- **Recommendation**: Once Finding 2's re-export cycle is broken,
  `from angee.base.resources.loader import build_resource` and
  `from angee.base.discovery import discover_addons` should move to module top.
  Re-check after the seam fix; if a residual cycle remains, the queryset
  method-local import of `loader` is the least-bad isolation point, but
  `discover_addons` should be top-level regardless (discovery depends on `apps`,
  not on resources).

### 10. `resources/entries.py` defers `fetch_url`, `json`, `yaml`
- **Lens**: B / C
- **Location**: `src/angee/base/resources/entries.py:136,203,212`
- **Severity**: Low
- **Problem**: `from angee.base.resources.fetch import fetch_url` is deferred in
  `materialize()`; `import json` and `import yaml` are deferred in
  `_read_structured`. `fetch.py` imports only stdlib + `entries.ResourceLoadError`
  (`fetch.py:12`) — a cycle (`entries → fetch → entries`). `json`/`yaml` are
  stdlib/declared deps, not optional.
- **Recommendation**: Move `import json` and `import yaml` to module top
  immediately (no cycle, not optional). For `fetch_url`, break the small cycle by
  moving `ResourceLoadError` out of `entries.py` into a leaf errors module that
  both `entries` and `fetch` import, then import `fetch_url` at top. `yaml` is
  not in `docs/stack.md`'s backend table though tablib pulls it — confirm it is a
  declared dependency (it is used unconditionally here).

### 11. Lifted resource options/branches with no consumer
- **Lens**: C
- **Location**: `src/angee/base/resources/entries.py` (binary handling),
  `src/angee/base/resources/managers.py:114-133`
- **Severity**: Low
- **Problem**: The binary-format machinery is half-built speculative generality:
  `BINARY_FORMATS`/`TABULAR_SUFFIXES` (`entries.py:52-57`) and
  `resolved_kind == "binary"` paths exist, but `_groups_for`
  (`managers.py:147`) raises "binary resources are not implemented yet" and
  `diff_addons` (`managers.py:127`) reports `0` rows for binary. The code carries
  a binary code path that always errors. Similarly `ResourceEntry.is_url`
  (`entries.py:110`) has no caller. "Add an abstraction only when it removes real
  duplication."
- **Recommendation**: Delete the unimplemented binary branch and the
  `is_url` property until a real consumer exists; treat unsupported suffixes as a
  single "unsupported format" error in `_tablib_format` (which already does this).
  Keep `BINARY_FORMATS` only if a near-term binary loader is planned; otherwise
  drop it.

### 12. `_resource_entry` preserves unknown keys "for future extension addons"
- **Lens**: C
- **Location**: `src/angee/base/apps.py:160-161`
- **Severity**: Low
- **Problem**: The docstring says unknown keys "are preserved for future
  extension addons but ignored by base." That is speculative generality — base
  reads `path`/`url`/`model`/`kind`/`encoding`/`depends_on`; any other key is
  silently carried and dropped. Silently accepting unknown keys hides typos
  (`depnds_on`) instead of failing fast, which the rest of the manifest layer
  does (`schema_parts` rejects unknown keys at `apps.py:296`).
- **Recommendation**: Either validate the entry key set and fail fast (matching
  `schema_parts`), or drop the "preserved for future" carry-through and document
  the closed key set. Do not keep an undocumented open extension point with no
  consumer.

### 13. `Lens D` — removing `.angee-manifest.json`
- **Lens**: D
- **Location**: writes `emission.py:107`; safety guard `emission.py:83` and
  `pipeline.py:94`; drift inclusion `emission.py:503` (`_is_checked_runtime_source`)
- **Severity**: Medium (decided; execution map)
- **Problem / map**: Three responsibilities depend on the manifest:
  1. **Write** — `emit_runtime_sources` writes `.angee-manifest.json` with
     `addons` / `resources` / `runtime_apps` (`emission.py:104-118`). The
     `_resource_manifest` helper (`emission.py:470`) exists *only* to populate
     this file; nothing reads the written manifest back. `runtime_apps`
     duplicates `RUNTIME_APPS` already emitted into `runtime/__init__.py`
     (`_runtime_init_source`, `emission.py:461`).
  2. **Reset/clean safety guard** — `reset_runtime_dir` (`emission.py:83`) and
     `clean_runtime` (`pipeline.py:94`) refuse to delete a directory unless it
     contains `.angee-manifest.json`, raising "is not an Angee runtime
     directory." This is the only *reader* of the manifest.
  3. **Drift check** — because the manifest is a generated source file,
     `_is_checked_runtime_source` (`emission.py:503`) currently includes it in
     `_generated_source_files`, so `check_runtime` diffs it like any other file.
- **What removal requires**:
  - Delete the write block (`emission.py:104-118` manifest portion) and the
    `_resource_manifest` helper (`emission.py:470-488`) entirely — it has no
    other consumer.
  - **Re-ground the safety guard without the manifest.** The host already passes
    an explicit, known `ANGEE_RUNTIME_DIR` (`settings.py:78`, host
    `settings.py:22`). A content marker is *not strictly needed* — the path is
    authoritative. But to keep the fail-safe against a mis-pointed dir, use the
    marker that survives: `runtime/__init__.py` contains
    `RUNTIME_APPS = [...]` (`emission.py:461`). Re-ground the guard on "does
    `runtime_dir/__init__.py` exist and define `RUNTIME_APPS`" (or simply
    "exists and is empty / contains only known runtime children"). Recommended:
    guard on the presence of the generated `__init__.py` marker, which the build
    always writes and which is far more robust than a separate manifest.
  - **`--check` stays correct by emit-to-string-and-diff alone.** `check_runtime`
    (`emission.py:51`) already emits to a temp tree and diffs file bytes; once
    the manifest is no longer written, it simply is not in either the expected or
    actual set, so the diff is unaffected. `_is_checked_runtime_source` needs no
    special-case for it. `check_schema_sdl` is independent. Confirmed: no git and
    no manifest marker are needed for `--check`.
- **Recommendation**: Drop the manifest write and `_resource_manifest`; switch
  both safety guards to test for the generated `runtime/__init__.py` (with
  `RUNTIME_APPS`) marker; leave `check_runtime`/`check_schema_sdl` otherwise
  untouched. This also removes the only reason `json` is imported in
  `emission.py` (verify after edit).

## Proposed `AngeeRuntime` shape

One class in `compose/` (e.g. `runtime.py`, replacing the public surface of
`emission.py`), owning the build target:

```python
@dataclass(slots=True)
class AngeeRuntime:
    addons: tuple[BaseAddonConfig, ...]
    extensions: dict[str, tuple[type[AngeeModel], ...]]
    labels: list[str]
    runtime_dir: Path

    @classmethod
    def plan(cls, addons, runtime_dir) -> "AngeeRuntime":
        extensions = _extensions_for(addons)          # stays module-private
        _check_field_collisions(addons, extensions)   # stays module-private
        return cls(addons, extensions, _runtime_labels(addons), runtime_dir)

    # lifecycle methods (were emission module functions):
    def emit_sources(self) -> None: ...
    def check_sources(self) -> None: ...            # was check_runtime
    def import_models(self) -> None: ...
    def normalize_migration_headers(self) -> None: ...
    def emit_schema_sdl(self) -> None: ...
    def check_schema_sdl(self) -> None: ...
    def reset(self) -> None: ...                    # was reset_runtime_dir
    def clean(self) -> None: ...                    # absorbs pipeline.clean_runtime
    def is_runtime_dir(self) -> bool: ...           # __init__.py / RUNTIME_APPS guard
```

- `RuntimePlan` is **deleted**; its three fields become `AngeeRuntime` fields,
  joined by the `runtime_dir` that every function currently passes alongside it.
- The stateless string builders (`_models_source`, `_class_import`,
  `_db_table_source`, `_rebac_meta_source`, `_history_excluded_fields`,
  `_runtime_init_source`, `_is_checked_runtime_source`, `_same_file`,
  `_write`, `_generated_source_files`) stay module-private functions the methods
  call — they own no instance state, so promoting them to methods would be
  ceremony, not clarity (per the guideline's "if the move creates more ceremony
  than clarity, stop").
- `pipeline.run` shrinks to a thin orchestrator with no owner of its own:
  ```python
  def run(*, addons=None, apply, check=False) -> BuildResult:
      runtime = AngeeRuntime.plan(addons or discover_addons(),
                                  Path(settings.ANGEE_RUNTIME_DIR))
      if check:
          runtime.check_sources(); runtime.check_schema_sdl()
          return BuildResult(len(runtime.labels), applied=False, checked=True)
      runtime.reset(); runtime.emit_sources(); runtime.import_models()
      runtime.emit_schema_sdl()
      ... makemigrations / normalize / migrate / sync_permissions ...
  ```
  `BuildResult`, `DriftError`, and the makemigrations/migrate/sync orchestration
  legitimately stay loose in `pipeline.py` — they coordinate Django management
  commands and rebac sync that no single runtime instance owns.
- `clean_runtime` (`pipeline.py:90`) folds into `AngeeRuntime.clean()`; the
  management command builds an `AngeeRuntime` (no plan needed for clean — it only
  needs `runtime_dir` and the guard) and calls `.clean()`.

Other dataclass-plus-sibling patterns checked: the resource value types
(`ResourceEntry`, `ResourceRow`, `ResourceGroup`, `LoadResult`,
`ValidationResult` in `resources/entries.py`) are **not** this smell — each
already carries its own behavior (`from_declaration`, `read_resource_rows`,
`to_dataset`, `loaded`), and the orchestration lives correctly on
`ResourceQuerySet` methods. `BuildResult` (`pipeline.py:27`) is a pure return
value with no sibling functions — fine. `DeletePreview`/`DeletePreviewGroup`
(`crud.py`) are pure GraphQL output types — fine. The runtime build is the only
genuine missing class.

## Inline-import inventory

| Location | Import | Reason it is deferred | Fix |
|---|---|---|---|
| `models.py:126` | `from angee.base.resources.models import Resource` (bottom re-export, `# noqa: E402`) | Real cycle: `resources.models` imports `AngeeModel` from here | Break the cycle (Finding 2); make it a normal top import or document as the one defensible deferred import |
| `apps.py:118` | `from angee.base.resources.models import Resource` | Symptom of the models↔resources cycle | Move to top after Finding 2 |
| `apps.py:240` | `from angee.base.models import AngeeModel` | Symptom of the cycle (apps→models→resources.models→...) | Move to top after Finding 2 |
| `apps.py:375` | `from angee.base.models import AngeeModel` | Same as above (duplicate of :240) | Move to top after Finding 2; dedupe with :240 |
| `apps.py:395` | `from angee.base.signals import register_revision_models` | Precautionary; no actual cycle | Move to top (Finding 8) |
| `signals.py:31` | `import reversion` | Cargo-culted; not optional (in INSTALLED_APPS) | Move to top (Finding 7) |
| `signals.py:32` | `from django.apps import apps` | Cargo-culted; runs in `ready()`, safe at top | Move to top (Finding 7) |
| `mixins.py:88` | `import reversion` | Not optional (in INSTALLED_APPS) | Move to top (Finding 6) |
| `resources/managers.py:40` | `from angee.base.resources.loader import build_resource` | Cycle via models↔resources re-export | Move to top after Finding 2; else least-bad isolation point |
| `resources/managers.py:68` | `from ...loader import DryRunRollback, build_resource, result_counts` | Same cycle | Move to top after Finding 2 |
| `resources/managers.py:170` | `from angee.base.discovery import discover_addons` | Cycle via apps→models | Move to top (discovery depends on apps, not resources) |
| `resources/entries.py:136` | `from angee.base.resources.fetch import fetch_url` | Small cycle: `fetch` imports `entries.ResourceLoadError` | Extract `ResourceLoadError` to a leaf errors module, then top import (Finding 10) |
| `resources/entries.py:203` | `import json` | None — stdlib, not optional | Move to top now (Finding 10) |
| `resources/entries.py:212` | `import yaml` | None — declared dep, not optional | Move to top now (Finding 10) |

(TYPE_CHECKING-only imports — `resources/managers.py:26`, `resources/entries.py:17`,
`resources/loader.py:29` — are correct and excluded; they avoid runtime cycles and
never execute.)

## Top recommendations

1. Collapse `RuntimePlan` + the ~14 emission/pipeline functions into one
   `AngeeRuntime` class with plan/emit/check/reset/import/normalize/sdl/clean
   methods; reduce `pipeline.run` to a thin orchestrator (Finding 1).
2. Break the `models.py` ↔ `resources/models.py` re-export cycle so the three
   `apps.py` deferred imports and the two `managers.py` `loader` imports move to
   module top (Findings 2, 9).
3. Remove `.angee-manifest.json`: delete the write and `_resource_manifest`,
   re-ground the reset/clean guard on the generated `runtime/__init__.py`
   /`RUNTIME_APPS` marker, and rely on emit-to-string-and-diff for `--check`
   (Finding 13).
4. Delete dead/speculative code: `iter_permission_paths` (Finding 3), the
   unimplemented binary-resource branch and `ResourceEntry.is_url` (Finding 11),
   and the argv fallback in `_running_angee_build` (Finding 4).
5. Move the non-cycle deferred imports (`reversion` in `mixins.py`/`signals.py`,
   `django.apps.apps`, `json`/`yaml`, `register_revision_models`) to module top
   (Findings 6, 7, 8, 10).
6. Tighten `_resource_entry` to fail fast on unknown keys like `schema_parts`
   does, instead of silently carrying them for hypothetical future addons
   (Finding 12); decide placement of `instance_from_public_id`/`public_id_of`
   (Finding 5).
