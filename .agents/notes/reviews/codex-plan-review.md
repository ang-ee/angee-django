### Verdict

Not ready to execute. Biggest risk: the plan has unresolved phase/layer contradictions, especially around schema SDL generation, addon discovery, command discovery, and `base.Resource` ownership.

### Plan findings

1. **Critical — Build is not actually emit-only**
   **Location:** §2.1, §2.4, Slice 8, §6  
   **Problem:** `emit()` still claims to write `runtime/<name>.graphql`, but GraphQL SDL needs concrete runtime models. `examples/notes-angee/src/example/notes/graphql.py` calls `apps.get_model("notes", "Note")`; on first build there is no runtime model, and on `--check` Django has already loaded stale runtime models during `django.setup()`.  
   **Fix:** Split SDL from emit. Make `angee build` write only model source, `runtime/__init__.py`, and permissions. Add an explicit fresh-process post-build phase for `makemigrations`, `migrate`, permission sync, and SDL render/check, or make `angee build` spawn that fresh process after emit. Specify the exact command surface and update `angee dev`.

2. **Critical — Addon discovery has no legal runtime owner**
   **Location:** §1.3, §1.4, §2.4, Slice 5, Slice 8  
   **Problem:** The plan moves `discover_addons()` to `angee.compose`, but `base/graphql/schema.py`, `base/asgi.py`, and `base/views.py` still need ordered addons while `base` may not import `compose`. `resources/management/commands/angee_resources.py` also needs discovery while `resources` may not import `compose`.  
   **Fix:** Keep installed-addon discovery in a runtime-neutral base owner, e.g. `angee.base.discovery` or a `BaseAddonConfig` registry method. Let `compose` import that owner. Update layering tests to enforce this direction.

3. **Critical — The `angee` management command is moved to an undiscovered package**
   **Location:** §1.3, Slice 8, `base/settings.py` contract  
   **Problem:** Django discovers management commands only from installed apps. The plan moves `angee` to `compose/management/commands/angee.py`, but `compose_defaults()` only says to install `angee.resources`, not `angee.compose`. `manage.py angee build` will disappear.  
   **Fix:** Either add a plain `angee.compose.apps.ComposeConfig` to `INSTALLED_APPS`, or keep the command in an installed app. If `compose` is installed, state that it is not a `BaseAddonConfig` and is excluded from addon discovery.

4. **High — `base.Resource` ownership contradicts the layer rule**
   **Location:** §1.1, §1.2, §1.4, §2.2  
   **Problem:** The plan says `base` imports neither `resources` nor `compose`, but `BaseConfig.source_model_modules = ("angee.resources.models",)` makes `base` know about a higher package. `BaseAddonConfig.resource_manifest` also currently depends on `ResourceTier` from the resource subsystem (`src/angee/base/apps.py`).  
   **Fix:** Choose the owner explicitly. Either move the ledger source model/tier vocabulary into `base` and keep loaders in `resources`, or make `angee.resources` contribute `Resource` to the `base` label through a compose-owned build-input contract. Do not leave `base` both “pure runtime” and responsible for importing `resources`.

5. **High — Current `source_model_modules` scanning will skip `angee.resources.models.Resource`**
   **Location:** §2.2, Slice 4; `src/angee/base/apps.py`  
   **Problem:** `_model_contributions` filters classes through `_belongs_to_source_module(value, self.name, package_prefix)`. For `BaseConfig.name == "angee.base"`, a class defined in `angee.resources.models` is outside the accepted prefix and will not emit as `base.Resource`.  
   **Fix:** Change ownership logic so explicitly listed `source_model_modules` are owned by that config. Add a test that `apps.get_app_config("base").model_classes` includes `angee.resources.models.Resource`.

6. **High — `_base_old` under `src/angee` breaks gates and packaging**
   **Location:** §3.1, Slice 0, §3.3  
   **Problem:** `git mv src/angee/base src/angee/_base_old` keeps old code importable, type-checked by `mypy src/`, linted by ruff, and packaged by `hatch` via `packages = ["src/angee"]`. It also violates the “behavioral reference only” intent.  
   **Fix:** Move the reference outside package source, preferably `.agents/reference/base_old`, or add explicit ruff/mypy/hatch excludes before Slice 0. Delete it before final verification.

7. **Medium — Per-slice gates are not executable as written**
   **Location:** §3.3, §4  
   **Problem:** Slice 1 asks for a Django-settings import before `base/apps.py` exists. Several slices reference tests “rewritten in Slice 8,” but the test rewrite is Slice 9. Full `mypy src/` also conflicts with the temporary `_base_old` tree.  
   **Fix:** Move each test rewrite into the slice that introduces the behavior, add a minimal `base/apps.py` scaffold before Django imports, and make gates reflect the actual partial tree.

8. **Medium — Manifest removal guard is underspecified**
   **Location:** §2.3, §2.4  
   **Problem:** “configured `runtime_dir` plus `runtime/__init__.py` with `RUNTIME_APPS`” is not a strong generated-output marker. Reset/clean semantics for stale app dirs, `schemas/`, `permissions.zed`, old `.angee-manifest.json`, `__pycache__`, and preserving migrations across removed labels are not precise.  
   **Fix:** Add an explicit generated sentinel in `runtime/__init__.py`, parse previous `RUNTIME_APPS` without importing arbitrary code, preserve `*/migrations/`, delete only known generated file classes, and define `reset()` versus `clean()` behavior.

### Guideline gaps

1. **Layer rule**
   Missing from `docs/backend/guidelines.md`. Add under “Django-Native Rule”:  
   `Backend packages are layered: angee.base is runtime and must not import angee.compose or angee.resources; angee.resources may import angee.base but not angee.compose; angee.compose may import angee.base and may consume resource declarations only during build-time composition.`

2. **Discovery owner**
   Ambiguous in `docs/backend/guidelines.md`. Add under “Framework Contracts”:  
   `Installed-addon discovery is a runtime registry read and must live in the lowest package that serves both runtime schema/resource commands and build-time composition; serving code must not import angee.compose just to enumerate addons.`

3. **Management command discovery**
   Missing from `docs/backend/guidelines.md`. Add under “Django-Native Rule”:  
   `A Django management command must live in an installed Django app; a non-addon package that owns commands must provide a plain AppConfig and must be excluded from BaseAddonConfig discovery.`

4. **Build/migrate command sequence**
   `AGENTS.md` still documents `angee build --no-apply`. Replace the one-shot example with the final split sequence, e.g.:  
   `uv run examples/notes-angee/manage.py angee build`  
   `uv run examples/notes-angee/manage.py makemigrations base notes`  
   `uv run examples/notes-angee/manage.py migrate`  
   plus the chosen permission/SDL step.

5. **Rewrite, do not copy**
   Missing from durable guidelines. Add to `docs/backend/guidelines.md` under “Framework Contracts”:  
   `For structural rewrites, preserve behavior from contracts and tests but write modules fresh; do not paste, mechanically port, or keep old modules importable unless an explicit compatibility promise requires it.`

6. **Import deferral scope**
   Ambiguous because docs allow optional dependency deferrals while the plan allows only Django phase-1 and `TYPE_CHECKING`. Add to `docs/backend/guidelines.md` import rule:  
   `Within src/angee framework packages, function-local imports are allowed only for AppConfig phase-1 model/signal deferrals and TYPE_CHECKING blocks; optional dependencies must be isolated behind modules with top-level imports.`

7. **External `source_model_modules` ownership**
   Missing from `docs/backend/guidelines.md`. Add under “Framework Contracts”:  
   `Modules listed in source_model_modules are explicit source-model inputs for that AppConfig, even when their dotted path is outside the app package; composition must not infer ownership only from package prefix.`

8. **Generated runtime deletion guard**
   Missing from `AGENTS.md` Mechanical Overrides. Add:  
   `A clean/reset command may delete only the configured generated runtime directory after verifying Angee's generated sentinel; it must preserve migration directories unless the command explicitly documents migration deletion.`

### Missing-from-plan checklist

- Add `py.typed` markers for the new typed packages (`base`, `compose`, `resources`).
- Specify `compose/apps.py` if `compose/management/commands/angee.py` remains in `compose`.
- Specify exact `__init__.py` exports, especially `angee.base.graphql`.
- Update `pyproject.toml` `testpaths` from `src/angee/base/resources/tests` to the new resources test path.
- Update all tests/examples importing `angee.base.resources.*` or `angee.base.compose.*`.
- Update tests expecting `get_commands()["angee_resources"] == "angee.base"`.
- Delete existing `__pycache__` files under `src/angee/base` and generated runtime trees.
- Remove old `.angee-manifest.json` from example runtime outputs.
- Add tests for first build from no runtime, stale-runtime `--check`, and SDL render after fresh runtime load.
- Update `angee dev` orchestration for the build → makemigrations → migrate → permissions/SDL sequence.
- Add layering tests that cover command packages and dynamic discovery ownership, not only static imports.
- Cover migration-label ripples: `base.Resource` migrates under `runtime.base.migrations`; `angee.resources` must not emit migrations.
