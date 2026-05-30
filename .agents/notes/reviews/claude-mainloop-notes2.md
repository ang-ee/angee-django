# Main-loop notes — pass 2 (classes / imports / lifted / manifest)

## Lens B — non-top-level imports (firsthand inventory)

TYPE_CHECKING (top-of-module, type-only — acceptable but mark the cycle they imply):
- loader.py:29 Resource; entries.py:17 BaseAddonConfig; managers.py:26 BaseAddonConfig.

Genuine function-local imports (the rule targets these):
1. entries.py:203 `import json`, :212 `import yaml` — NO good reason (json is stdlib).
   Easiest fix: move to top. yaml is a locked dep. → top.
2. signals.py:32 `from django.apps import apps` — move to top (apps is always importable
   inside a function that only runs at ready()). reversion (:31, mixins.py:88) is the
   one arguably-optional heavy dep → could stay or isolate.
3. The cycle-driven cluster (the real architecture smell):
   - apps.py:118 Resource, :240/:375 AngeeModel — apps ↔ models/resources.models cycle.
   - entries.py:136 fetch_url — entries ↔ fetch cycle (fetch imports entries.ResourceLoadError).
   - managers.py:40/:68 build_resource (loader), :170 discover_addons — resources.models
     → managers → loader → widgets → models → resources.models knot.
   ROOT CAUSE: `models.py` re-exports `Resource` from `resources/models.py` (bottom
   import) while `resources/*` imports back up to `models.AngeeModel`. Breaking this one
   cycle (e.g. AngeeModel/helpers in a module the resources package doesn't import back,
   or drop the re-export and discover Resource another way) removes most deferred imports.
4. apps.py:395 register_revision_models (in ready) — Django ready()-time wiring; could be
   top-level import of signals (signals doesn't import apps). Likely movable.

## Lens D — manifest references (3 sites)
- emission.py:107 — WRITES `.angee-manifest.json` (addons/resources/runtime_apps).
- emission.py:83 — reset guard: "manifest exists → is an Angee runtime dir".
- pipeline.py:94 — clean guard: same.
Removing: stop writing it; re-ground the reset/clean guard. The host already passes an
explicit ANGEE_RUNTIME_DIR, so the destructive guard can key off that known path (or a
lighter marker like the generated `__init__.py` with RUNTIME_APPS) instead of the
manifest. `_is_checked_runtime_source`/`_generated_source_files` then drop the manifest
special-casing. `--check` already emits-and-diffs (check_runtime), so it's unaffected.
Also: is `resources` even consumed from the manifest anywhere? If nothing reads it, the
whole manifest is dead output → delete (Lens C overlap).

## Lens A — compose onto classes
- compose/emission.py (module fns + passive RuntimePlan) + compose/pipeline.py (run,
  clean_runtime, BuildResult, DriftError) → one `AngeeRuntime` class owning
  plan/emit/check/reset/import/normalize/sdl/build/clean. RuntimePlan fields become its
  state (addons/extensions/labels); _emit_addon/_models_source/_write etc. become private
  methods or stay module helpers if genuinely stateless string-building.
- Check other dataclass+function clusters: resources/entries.py ResourceEntry already has
  methods (good); ResourceGroup has to_dataset (good). graphql/schema.py is module fns
  over no owning object — borderline (could be a SchemaRegistry but lower value).

## Lens C — lifted/unearned (candidates)
- .angee-manifest.json (if unread → fully dead).
- ResourceEntry.is_url property (entries.py) — used anywhere? check.
- emission BuildResult fields (emitted/applied/checked) — only for a stdout line; keep?
- Verify every crud() param (permission_classes, name) is exercised.
