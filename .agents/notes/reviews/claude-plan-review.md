# Plan Review — compose / base / resources refactor

Reviewer: Claude subagent. Reviewed: `.agents/plans/2026-05-30-compose-base-resources-refactor.md`
against `AGENTS.md`, `docs/guidelines.md`, `docs/backend/guidelines.md`, `docs/stack.md`,
`docs/glossary.md`, and the current tree under `src/angee/base/`.

## Verdict

Not ready to execute as written. The three-layer split is sound and the import-at-top /
compose-onto-classes goals are achievable, but the plan has **two correctness-breaking
gaps** that an executor following only the plan would ship broken: (1) §2.1's "emit-only
build" is wrong about what emit needs — SDL emission and `--check` both require the
*concrete* runtime models in the registry (current `emit_schema_sdl` / `check_schema_sdl`
call `import_runtime_models` precisely for this, and `examples/.../notes/graphql.py` does
`apps.get_model("notes","Note")`), so emit cannot be purely source-driven; and (2) the
`angee` command is moved into `angee.compose`, which nothing installs into `INSTALLED_APPS`
and which has no `AppConfig`, so `manage.py angee build` becomes undiscoverable. Biggest
risk: the build-split design is asserted, not traced, and the surrounding orchestration
(`templates/stacks/dev/.../angee.yaml.jinja` with `--no-apply`, `--watch`, `angee assets`,
`rebac sync`) is never reconciled with the new command surface.

## Plan findings

### 1. (Critical) Emit-only build still needs concrete models for SDL and `--check`
- **Location:** §2.1, §2.3, §2.4 (`render_sources()` / `check()` / "Emission consumes
  source models only").
- **Problem:** The plan claims "Emission consumes source models only, so whatever the
  registry holds is irrelevant." That is false for the SDL half of emission. Current
  `src/angee/base/compose/emission.py:144` (`emit_schema_sdl`) and `:157`
  (`check_schema_sdl`) both call `import_runtime_models(plan)` first, because
  `render_sdl` → `build_schema` → `strawberry.Schema(...)` instantiates strawberry-django
  types that resolve **concrete** models. The example proves it:
  `examples/notes-angee/src/example/notes/graphql.py:12` does
  `Note = apps.get_model("notes", "Note")` at module import. So SDL rendering requires the
  emitted `runtime/<label>/models.py` to be importable *and registered* — which on a first
  build (no prior `runtime/`) do not exist yet, and which during `--check` the plan wants
  to compare without a build. The "render to an in-memory `{relative_path: text}` map and
  diff" story in §2.3 covers the model `.py` files (those *are* pure source renders) but
  **not** `schemas/*.graphql`; the current code splits these deliberately
  (`_is_checked_runtime_source` excludes `.graphql`, `emission.py:503`). The plan folds SDL
  into `render_sources()`/`check()` and drops that distinction.
- **Fix:** Split the contract explicitly in §2.1/§2.4. `AngeeRuntime.render_sources()`
  renders only the pure source files (models, `__init__.py`, `permissions.zed`) and needs
  nothing from the registry — that part of "emit-only, no flag" is correct. SDL is a
  *second phase* that runs after the just-emitted concrete models are importable. Decide
  and document where that phase runs: either (a) `emit()` writes models, then re-imports
  the fresh runtime in the same process to render SDL (this is exactly the stale-cache
  problem the flag existed for — see finding 2), or (b) SDL emission/`--check` move into a
  separate post-`makemigrations` process that does a clean `django.setup()` and imports the
  concrete models normally. State which, and keep `check_schema_sdl`'s "import then render
  then diff" behavior — do not collapse SDL drift into the source-text diff.

### 2. (Critical) The flag is not eliminated by splitting steps unless emit stops re-importing in-process
- **Location:** §2.1 ("no flag, no suppression"), self-review checklist item 2.
- **Problem:** `_running_angee_build()` (`src/angee/base/apps.py:43`) exists because
  `import_models()` (`apps.py:360`) eagerly imports `runtime/<label>/models.py` during
  `apps.populate()` at `django.setup()`. The flag suppresses that import *only* while the
  same process is about to regenerate and re-import those files (the in-process
  `import_runtime_models`, `emission.py:121`, deliberately calls
  `importlib.invalidate_caches()`). The plan's claim that splitting `makemigrations` into a
  later process removes the need for the flag is only true **if `emit()` itself never
  imports the runtime in the build process**. But finding 1 shows SDL rendering wants the
  concrete models in-process. If the executor keeps SDL emission inside `angee build`'s
  process (the plan's "Default: `angee build` emits + checks"), then at that process's
  `django.setup()` `import_models()` will have already adopted the *stale* prior runtime
  (no flag to suppress it), and re-importing the freshly emitted module collides /
  double-registers. So the flag is not actually gone; it has only moved to a different
  symptom.
- **Fix:** Make the elimination explicit and verifiable. The only clean design that drops
  the flag is: `angee build` emits source text *only* (no `import_models` of the runtime
  needed because nothing in emit touches concrete models), and a **separate process** does
  `makemigrations` + SDL render + `--check` after a fresh `django.setup()` that adopts the
  now-current runtime via the unchanged `import_models()`. Spell out in §2.1 that SDL
  rendering and SDL `--check` live in that second process, not in `angee build`. Add the
  §5 test the plan already promises ("emit + a separate `makemigrations` produces a correct
  non-stale migration without any build flag") **and** an equivalent test that SDL renders
  correctly from the separate process — otherwise the executor cannot tell the design works.

### 3. (Critical) `angee` command moved to `angee.compose`, which is never installed
- **Location:** §1.3 (`compose/management/commands/angee.py`), §1.1/§7 (`compose_defaults`
  "adds `angee.resources` to INSTALLED_APPS"), Slice 8.
- **Problem:** Django discovers management commands only from apps in `INSTALLED_APPS`.
  Today `manage.py angee build` works because the command lives under `angee.base`, and
  `BaseConfig` is installed (`tests/settings.py:12`, `compose_defaults` line in
  `src/angee/base/settings.py:54`). The plan moves the command into `angee.compose`, but
  `compose` has **no `AppConfig`** and `compose_defaults` is documented to add only
  `angee.resources` (§1.1). Nothing puts `angee.compose` on `INSTALLED_APPS`, so
  `manage.py angee build` will not be found. `compose` is also described as build-only and
  "never on the serving path," which makes installing it as a normal app awkward.
- **Fix:** Choose one and write it into §1.3/§2.x: (a) give `angee.compose` a plain
  `AppConfig` and have `compose_defaults` add `angee.compose` to `INSTALLED_APPS`
  (accepting that a build-time app is loaded at serve time — it imports `base` only, so it
  is inert), or (b) keep the `angee` build command under `angee.base` (which is always
  installed) and let it import `AngeeRuntime`/`discover_addons` from `angee.compose` at
  *command-handler* time — the command is build-time orchestration, so `base`→`compose` at
  handler call time does not violate the runtime/serving rule, but it does violate the
  blanket "`base` imports neither `compose` nor `resources`" wording and the layering test
  in §1.4/Slice 9. Pick (a) or (b) and update both the layering rule text and the layering
  test scope accordingly.

### 4. (Critical) The resources command cannot discover addons without importing compose
- **Location:** §1.2 (`resources/management/commands/angee_resources.py` "discovers addons
  and passes them in"), §2.4 ("ResourceManager does not call `discover_addons` … the
  management command / `AngeeRuntime` discovers and passes them"), §1.4 ("`resources`
  imports `base`, never `compose`").
- **Problem:** `discover_addons` is placed in `compose/discovery.py` (§1.3). The plan
  removes the `managers → discovery` edge by having the *command* discover and pass
  `addons`. But that command lives in `angee.resources`, which "never imports compose." So
  the command would have to import `discover_addons` from `compose` — a direct
  `resources → compose` violation that the new layering test (Slice 9) is meant to catch.
  Verified the real edge today: `src/angee/base/resources/managers.py:14` imports
  `discover_addons`, and `_entries_for` (line 155) calls it; the command
  (`angee_resources.py`) calls `objects.validate_addons(tiers=...)` with no addons, relying
  on that default. Moving discovery to `compose` strands the resources command.
- **Fix:** Put `discover_addons` where both `resources` and `compose` may import it: it
  reads the Django registry for `BaseAddonConfig` instances and depends only on
  `angee.base.apps` (confirmed: `src/angee/base/discovery.py` imports only
  `angee.base.apps`). It is a `base`-level registry query, not a build-time concern. Move
  `discover_addons` to `angee.base` (e.g. `base/discovery.py` or a method/classmethod on
  `BaseAddonConfig`, which the find-the-owner rule arguably prefers). Then
  `compose.runtime` and `resources` command both import it from `base` with no upward edge.
  Update §1.2/§1.3/§2.4 and the old→new mapping in §8.

### 5. (High) `reset()`/`clean()` guard re-grounding can refuse a legitimate first/old runtime, or fail to protect
- **Location:** §2.3 (re-ground guard on `runtime/__init__.py` carrying `RUNTIME_APPS`).
- **Problem:** Two failure modes. (a) On a **first build** there is no `runtime/__init__.py`
  yet, so a guard that *requires* the marker before deleting is fine for empty dirs but the
  plan must state that a non-empty directory lacking the marker is refused — the current
  code refuses exactly that (`emission.py:83`, `pipeline.py:94`). (b) On a **clean checkout
  of the example**, `runtime/__init__.py` exists and carries `RUNTIME_APPS` (verified:
  `examples/notes-angee/src/runtime/__init__.py`), so the marker works there — but the
  marker is itself generated output the guard is about to overwrite, so the guard must read
  it *before* writing anything, and must treat "dir exists, non-empty, no marker, not the
  configured `runtime_dir`" as a hard refusal. The plan says "refuse to delete a directory
  that is not the configured runtime dir / lacks that marker" but does not pin the
  precedence (configured-path check first, marker second) or the empty-dir allowance.
- **Fix:** In §2.3 state the guard precisely: refuse unless `path == settings.ANGEE_RUNTIME_DIR`
  (resolved) AND (`path` is empty OR `path/__init__.py` defines `RUNTIME_APPS`). Keep the
  per-addon migration-preserving reset (`_reset_addon_dir`, `emission.py:530`) as a method
  on `AngeeRuntime`. Add a test that a non-runtime non-empty dir is refused and that a
  first build into an empty configured dir is allowed.

### 6. (High) Dev orchestration template references a command surface the plan never reconciles
- **Location:** §2.1 (command surface), and unaddressed:
  `templates/stacks/dev/template/{{ ANGEE_ROOT }}/angee.yaml.jinja`.
- **Problem:** The live dev orchestration runs `manage.py angee build --no-apply` (job
  `build`, line 59), `manage.py angee build --watch --no-apply` (service `build-watch`,
  line 123), `manage.py migrate --noinput` (job `migrate`, line 71), `manage.py rebac sync
  --yes` (job `permissions`, line 84), and `manage.py angee assets load install/demo` (job
  `assets`, lines 99-101). The plan's new `angee` command exposes only `build` (emit+check)
  and `clean`. `--no-apply`, `--watch`, and the `angee assets` subcommand are **not in the
  plan at all** (`--watch`/`assets` do not even exist in the current command —
  `src/angee/base/management/commands/angee.py` has only `--no-apply`/`--check`/`clean`, so
  the template is already ahead of code). If the executor builds the plan's surface, the
  dev stack breaks: `--no-apply` and `--watch` flags vanish and `angee assets` is missing.
- **Fix:** Add a section to the plan that fixes the command surface end to end: decide the
  flags `build` keeps (the plan's emit-only build makes `--no-apply` meaningless — say so
  and update the template to drop it), define or explicitly defer `--watch` and
  `angee assets`, and list the exact template edits in
  `templates/stacks/dev/.../angee.yaml.jinja`. The plan's §2.1 "Decision … to confirm"
  note must be resolved, not left open, before Codex runs.

### 7. (High) `base/graphql/__init__.py` re-export vs. the "avoid `__all__`" rule
- **Location:** §1.1 (`base/graphql/__init__.py` "Re-export crud, changes, ChangeEvent,
  schema helpers"), backend guideline "Avoid `__all__` unless a module has a concrete
  star-import or compatibility requirement."
- **Problem:** The current `src/angee/base/graphql/__init__.py` defines `__all__` (verified)
  and is a pure re-export facade — the example imports `from angee.base.graphql import
  changes, crud`. The plan says "rewrite, don't copy" and the guideline says avoid `__all__`,
  but a re-export package is the canonical legitimate use. An executor following the
  guideline literally would delete `__all__`; one following the old code would keep it.
  Ambiguous.
- **Fix:** Decide in §1.1: a re-export `__init__` is the public API surface, so `__all__` is
  the "compatibility requirement" the guideline allows; keep it. (Also see guideline gap 6.)

### 8. (Medium) `pyproject` testpaths/pythonpath and `tests/runtime` are under-specified
- **Location:** §5, §9 ("Update `pyproject.toml` testpaths/pythonpath if paths move"),
  `tests/settings.py`.
- **Problem:** Current `pyproject.toml` sets `testpaths = ["tests",
  "src/angee/base/resources/tests"]` and `pythonpath = [".", "src",
  "examples/notes-angee/src"]` (verified). The plan moves resource tests to
  `src/angee/resources/tests` (path change) but §9 says only "if paths move" — they do
  move, so this is mandatory, not conditional. Also `tests/settings.py:22` sets
  `ANGEE_RUNTIME_MODULE = "tests.runtime"`; the layering/build tests that emit need a
  writable runtime dir/module — the plan does not say what `ANGEE_RUNTIME_DIR` the new
  build/SDL tests use.
- **Fix:** In §9 make the testpaths edit explicit (`src/angee/base/resources/tests` →
  `src/angee/resources/tests`) and state the test runtime dir/module the new emit+
  makemigrations test uses (a `tmp_path` runtime dir + a settings override, or a dedicated
  `tests/settings.py` value).

### 9. (Medium) `migrate` ordering across `base` and addon labels is assumed, not stated
- **Location:** §2.1 ("`migrate` … as a separate later step"), §2.2.
- **Problem:** The example's generated migrations have `dependencies = []`
  (`runtime/base/migrations/0001_initial.py:9`; notes likewise). With no cross-app FK from
  `notes`→`base` today this is fine, but the plan introduces no rule for inter-label
  migration dependencies. If a consumer addon FKs `base.Resource`, the autodetector must
  see `base` already migrated; running `migrate` (all apps) after `makemigrations` handles
  this, but only if `makemigrations` is invoked for all labels together (current
  `pipeline.run` passes `*plan.labels`, `pipeline.py:74`). The plan's "separate later step"
  must still `makemigrations` for the full label set in one invocation.
- **Fix:** State in §2.1 that the separate step runs `makemigrations <all runtime labels>`
  in one call (preserving current behavior) before `migrate`, so cross-label dependencies
  resolve.

### 10. (Medium) `__init__` exports / namespace-package rule only partially called out
- **Location:** §1, §3 (Slice 0 "no `src/angee/__init__.py`"), §8 checklist.
- **Problem:** §1 correctly says keep `angee` a namespace package, but the plan does not
  enumerate the `__init__.py` contents for the three new packages beyond "with `__init__.py`".
  `base/__init__.py` today is a docstring only (verified). The plan should say the package
  `__init__`s stay thin (docstring; no eager imports that would re-create cycles or import
  models at package import), and explicitly that there is no `src/angee/__init__.py`,
  `src/angee/base/__init__.py` does not import `apps`/`models`, etc. The `py.typed` marker
  in `src/angee/base/py.typed` also needs a home decision (per-package `py.typed` for
  `base`/`resources`/`compose`, or one).
- **Fix:** Add to §1 a one-line rule: each package `__init__.py` is a docstring only (no
  eager submodule imports); ship `py.typed` in each of the three packages; reaffirm no
  `src/angee/__init__.py`.

### 11. (Low) Old→new mapping is correct but the stale-`.pyc` modules could mislead
- **Location:** §8 checklist (old module inventory).
- **Problem:** `__pycache__` under `src/angee/base/` contains stale `.pyc` for modules that
  no longer exist as source (`computed`, `graphql.py`, `resources.py`, `managers.py`,
  `emission.py` at the top level). Verified these have no corresponding `.py`. An executor
  grepping pycache could think a top-level `base/managers.py` or `base/computed.py` must be
  mapped. The real current source set matches the plan's mapping (apps, mixins, models,
  signals, graphql/*, views, consumers, asgi, urls, settings, discovery, compose/*,
  management/*, resources/*). One genuinely unmapped current file: `src/angee/base/py.typed`
  (see finding 10).
- **Fix:** Add a Slice 0 step to delete stale `__pycache__` before/after `git mv`, and add
  `py.typed` to the §8 mapping.

## Guideline gaps

1. **The Django app-loading import exception is well-stated; the *optional dependency*
   exception is under-specified for this repo's "no try/except imports" rule.**
   - Where: `docs/backend/guidelines.md` "Imports go at the top" bullet allows "a
     dependency that is genuinely optional at runtime (isolate it behind its own module)."
     The executor also must satisfy the user-memory rule "never wrap imports in
     try/except." The current `_module_exists` (`apps.py:65`) is the established pattern for
     "import the runtime only if it exists" without try/except, and the plan's `import_models()`
     adoption relies on it — but the guideline never names this pattern.
   - Edit (`docs/backend/guidelines.md`, Imports bullet): append a sentence —
     "Probe optional/generated modules with `importlib.util.find_spec` (verifying each
     parent package) rather than `try/except ImportError`; an absent generated `runtime/`
     tree must read as 'not built yet,' not as a swallowed error."

2. **Build-time vs. serving-path import direction needs an explicit rule, not just the
   layering bullets.** The executor needs to know that a management *command handler* (a
   build entrypoint) may import the build layer even when it physically lives in an
   always-installed app — this is the crux of finding 3/4.
   - Where: `docs/backend/guidelines.md` "Use symbolic model references … avoid import
     cycles" and the Django-Native Rule say nothing about the serving-path vs. build-time
     import boundary.
   - Edit (`docs/backend/guidelines.md`, add a bullet under Rules): "Separate build-time
     from serving-path imports by *when* they run, not only by module: a management command
     or build entrypoint may import the composer at handler-call time even if its module is
     installed for serving; never import the composer at module top in a serving module
     (`asgi`, `urls`, `views`, `consumers`, `signals`, `models`)."

3. **"Rewrite from guidelines, do not copy" lives only in the plan, not the guidelines.**
   The brief requires Codex to need only guidelines + plan. The rewrite-not-copy rule is a
   process rule that belongs where process rules live.
   - Where: `docs/guidelines.md` has no statement on reconstructing vs. copying lifted code;
     `AGENTS.md` says "Prefer deletion to abstraction" and "Compose at build time" but not
     "reconstruct, don't paste."
   - Edit (`docs/guidelines.md`, under Coding Principles or a new "Rewrite, don't port"
     subsection): "When restructuring or lifting existing code, reconstruct each module from
     its contract and these guidelines; do not paste from the old tree. Pasted code carries
     the old structure's compromises into the new one."

4. **Settings helper must add the build/command-host app — the rule that an app must be in
   `INSTALLED_APPS` for its command to be discovered is implied, not stated.**
   - Where: `docs/backend/guidelines.md` "Command dispatch lives in Django management
     commands" (Django-Native Rule) does not state the discoverability constraint, which is
     exactly what finding 3 trips on.
   - Edit (`docs/backend/guidelines.md`, Framework Contracts, settings-helper paragraph):
     add — "A package contributes management commands only when it is in `INSTALLED_APPS`;
     a settings helper that composes a host must install every app whose commands the host
     needs (the composer/build app and the resources app), not only the addons that emit
     models."

5. **"Compose behavior onto classes" is clear, but *when a loose function is still allowed*
   is stated twice with slightly different lists — and the plan's `_models_source`/
   `_class_import` pure renderers sit exactly on that line.**
   - Where: `docs/guidelines.md` "Put Behavior on the Owning Object" lists the allowances
     (orchestration / pure transform with no owner / integration entrypoint);
     `docs/backend/guidelines.md` repeats "Keep a module-level function only for
     orchestration that genuinely has no owner." The plan §2.4 says the string-render helpers
     "may stay module-level pure renderers owned by the class, or become private methods —
     decide by cohesion." An executor needs a tie-breaker.
   - Edit (`docs/backend/guidelines.md`, the compose-onto-class bullet): add the tie-breaker
     — "A pure renderer that takes the owner and returns text with no other state may stay a
     module-level function in the owner's module; make it a method only if it reads more
     than one field of the owner or shares state with sibling renderers."

6. **`__all__` policy conflicts with re-export facades.** (Drives finding 7.)
   - Where: `docs/backend/guidelines.md` "Avoid `__all__` unless a module has a concrete
     star-import or compatibility requirement."
   - Edit: append — "A package `__init__.py` whose sole purpose is to re-export a stable
     public API (e.g. `graphql/__init__.py`) is a compatibility surface; declaring `__all__`
     there is allowed and expected."

7. **Docstring scope ("what must have one") is stated but the manifest-attribute and
   declarative-constant cases need a one-line anchor for the executor.** The plan repeatedly
   says "every public symbol and manifest attribute gets a docstring."
   - Where: `docs/backend/guidelines.md` Framework Contracts already says "Add docstrings to
     public modules, classes, methods, functions, and declarative manifest attributes." This
     is adequate; the only gap is that module-level *constants* like `SCHEMA_PART_KEYS`
     (`apps.py:29`) and `DEFAULT_SCHEMA_NAME` (`schema.py:28`) are public symbols the plan
     expects documented but the rule lists only "manifest attributes."
   - Edit (Framework Contracts, docstring sentence): change "and declarative manifest
     attributes" to "and declarative manifest attributes and public module-level constants."

8. **Three-layer dependency rule ("base imports nothing upward") is in the plan but not in
   any doc** — so a future change outside this refactor has no durable rule to honor.
   - Where: `AGENTS.md` Repository Role describes framework/base/consumer *levels* but not
     the `base` / `resources` / `compose` package dependency direction; the backend
     guideline's layering hints are example-flavored.
   - Edit (`docs/backend/guidelines.md`, add to Rules): "The framework core splits into
     `angee.base` (runtime), `angee.resources` (resource subsystem), and `angee.compose`
     (build-time). Dependencies flow one way: `resources → base` and `compose → base`;
     `base` imports neither, and `resources` never imports `compose`. Registry-discovery of
     addons is a `base`-level query shared by both upper layers." (This also resolves
     finding 4's owner question.)

## Missing-from-plan checklist

- Where SDL is rendered and SDL `--check` runs given emit no longer imports the runtime
  (findings 1, 2) — the plan must name the process and the loaded models.
- How `angee.compose` becomes a discoverable command host (`AppConfig` + `INSTALLED_APPS`),
  or the decision to keep the `angee` command under `base` (finding 3).
- The home of `discover_addons` so the resources command and compose can both use it without
  a `resources → compose` edge (finding 4).
- Reconciling `templates/stacks/dev/template/{{ ANGEE_ROOT }}/angee.yaml.jinja`: `--no-apply`,
  `--watch`, `angee assets load`, and `rebac sync` against the new command surface
  (finding 6). `angee assets` and `--watch` do not exist in code today and are unplanned.
- The exact `pyproject.toml` testpaths/pythonpath edits and the runtime dir/module the new
  emit-and-makemigrations test uses (finding 8).
- `migrate`/`makemigrations` invocation shape in the separate step (one `makemigrations`
  call for all labels) to keep cross-label migration dependencies resolvable (finding 9).
- `py.typed` placement for the three packages and the thin-`__init__` rule (findings 10, 11).
- Deleting stale `__pycache__` under the moved tree so the old→new audit is not misled
  (finding 11).
- `compose_defaults` test for "single install" must now also assert the
  build/command-host app is installed (ties to finding 3); §5 lists only `angee.resources`.
- `REBAC`/`rebac sync`: the dev stack runs `manage.py rebac sync` (library command) for
  permissions, while the in-process build calls `sync_permissions()` (`pipeline.py:82`).
  The plan keeps `compose/rebac.py: sync_permissions` but does not say whether the separate
  migrate step or the library `rebac sync` owns permission sync now — clarify to avoid two
  competing sync paths.
