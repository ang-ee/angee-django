# Pass 2 synthesis — classes / imports / lifted code / manifest

Four views (main-loop, Claude subagent, Gemini, Codex) on the refactored core.
Ranked by consensus × severity. Agreement: M S G C.

## Tier 1 — unanimous, do first

### A1. `AngeeRuntime` class — runtime composition has no owner  [M S G C — all four]
`RuntimePlan` (emission.py) is a passive 3-field dataclass; ~14 module functions across
`compose/emission.py` + `compose/pipeline.py` (`plan_runtime`, `check_runtime`,
`reset_runtime_dir`, `emit_runtime_sources`, `import_runtime_models`,
`normalize_migration_headers`, `emit_schema_sdl`, `check_schema_sdl`, `run`,
`clean_runtime`) thread that plan + `runtime_dir`. The exact "missing class" the new
guideline names. → One `AngeeRuntime` class owning runtime_dir/data_dir/runtime_module/
addons/extensions/labels, with `from_settings(addons=None)` / `from_addons(...)`
constructors and methods `check/reset/emit_sources/import_models/emit_schema_sdl/
check_schema_sdl/normalize_migration_headers/build(apply,check)/clean`. `RuntimePlan`
disappears (becomes the object's own state); `pipeline.run`/`clean_runtime` become thin
command-facing wrappers. **Key:** `render_sources()` returns a deterministic
`{relative_path: text}` map so `--check` diffs rendered strings vs disk — no manifest.

### B1. The `models.py` ↔ `resources/models.py` re-export cycle  [M S G C]
`models.py` bottom-imports `Resource` (so the composer discovers it in base's models
module) while `resources/models.py` imports `AngeeModel` back up. This one cycle forces
the deferred imports in `apps.py` (:118, :240, :375). → Give `Resource` one explicit
owner: define it in `angee.base.models` (its conventional source-models module); keep
the resources *machinery* (manager/widgets/loader/entries/ordering/fetch) in
`base/resources/`. Removes the re-export and 3 deferred imports.
DECISION: this moves the `Resource` *model* (not the machinery) to `models.py`. Confirm
that's consistent with "keep resources in base/resources" (machinery stays; only the
ledger model relocates to where base models live).

### D. Remove `.angee-manifest.json`  [M S G C]
3 sites: written at `emission.py:107` (+ `_resource_manifest` helper — no other reader),
reset guard `emission.py:83`, clean guard `pipeline.py:94`, drift-listed `emission.py`
(`_is_checked_runtime_source`). Verified: its `runtime_apps` duplicates `RUNTIME_APPS`
already in `runtime/__init__.py`; nothing reads the manifest except the guards. →
Stop writing it; drop `_resource_manifest`; re-ground the reset/clean destructive guard
on the explicit `runtime_dir` (the host passes it) — optionally also require the
generated `runtime/__init__.py` with `RUNTIME_APPS` as a content marker. `--check` is
unaffected (already emit-and-diff).

### B2. Hoist non-cycle deferred imports to top  [M S G C]
Clear wins (no cycle, just hoist): `entries.py:203 import json`, `:212 import yaml`,
`mixins.py:88 import reversion`, `signals.py:31 reversion`/`:32 django.apps`,
`apps.py:395 register_revision_models`. The cycle-driven ones (apps.py:118/240/375,
managers.py:40/68/170, entries.py:136 fetch_url) hoist *after* B1 + the fetch fix.

## Tier 2 — strong, mostly agreed

### B3. `entries.py` ↔ `fetch.py` mini-cycle  [C, S]
`fetch.py` imports `ResourceLoadError` from `entries.py`, so `entries.materialize`
defers `fetch_url`. → Move `ResourceLoadError` to a leaf `resources/exceptions.py`; then
both import it and `entries` top-imports `fetch_url`.

### C1. Delete unearned `kind` / binary-resource surface  [C, S]
The manifest accepts `kind`, `entries.py` classifies `BINARY_FORMATS`, `diff_addons`
has a binary special-case — but loading immediately raises "binary resources are not
implemented yet." Speculative. → Delete `kind`, `BINARY_FORMATS`, binary classification,
the `diff_addons` binary branch, and the binary test, until binary is real.

### C2. Delete verified dead code  [S — verified by main-loop]
- `compose/rebac.py:13 iter_permission_paths` — zero callers. Delete.
- `resources/entries.py:111 ResourceEntry.is_url` — zero callers. Delete.

### A2. Resource loader helpers should be `AngeeResource` methods  [C]
`_row_xref`, `_row_content_hash`, `_adopt_existing_target`, `_restore_auto_fields`
(loader.py) operate on `AngeeResource` state from outside → make them private methods;
consider `AngeeResource.build(...)` classmethod factory replacing `build_resource`.

### A3. GraphQL schema composition is a passive dict  [C]
`SchemaParts` dict threaded through `collect_schema_parts/collect_schema_names/
build_schema/render_sdl`, re-discovering addons each call. → optional `GraphQLSchemas.
from_addons(addons)` with `names()/build(name)/render_sdl()`. Lower priority than A1.

## Decisions needed (no consensus / behavior changes)

- **`_adopt_existing_target`** (loader.py:357, CALLED at :117 — NOT dead): Gemini says
  delete (adoption-by-unique-field is speculative/dangerous across envs); Codex says
  keep but move onto `AngeeResource`. It's load-bearing (demo load adopts existing rows
  e.g. `auth.User`). → keep+move-to-method, OR remove the adoption feature? (behavior)
- **`_running_angee_build` argv fallback**: subagent says require `ANGEE_BUILDING=1`,
  drop argv. Risk: bare `manage.py angee build` (no launcher) wouldn't set it → imports
  stale runtime. Keep argv unless the launcher guarantees the env. The AngeeRuntime
  refactor may reshape this anyway.
- **instance_from_public_id/public_id_of type-switch** (models.py): subagent flagged the
  AngeeModel-vs-plain branch; it's REQUIRED (resource targets like `auth.User` aren't
  AngeeModel). Correct as-is — not actionable.

## Proposed implementation order (each phase verified green + docstrings per guideline)
1. **C2 + C1** — delete dead code (`iter_permission_paths`, `is_url`) and the kind/binary
   surface. Smallest, unblocks clarity.
2. **B1 + B3** — break the cycles (move `Resource` to `models.py`; `ResourceLoadError` →
   `resources/exceptions.py`), then **B2** hoist all deferred imports to top.
3. **A1** — introduce `AngeeRuntime`; collapse emission+pipeline onto it; `--check` via
   rendered `{path:text}` map; **D** drop the manifest in the same pass.
4. **A2** (loader methods) and **A3** (GraphQLSchemas) — optional, after A1 lands.
Docstrings on every new/changed public symbol throughout (backend guideline).
