### Summary
The biggest remaining issue is that runtime composition still has no owning object: `RuntimePlan` is passive data and the actual behavior is scattered across `compose/emission.py` and `compose/pipeline.py`. That same owner leak drives the manifest problem: `.angee-manifest.json` is still used as a generated marker even though the host already supplies the explicit runtime directory. The import violations are mostly not isolated style issues; they expose a real `models.py` / `resources.models` ownership cycle and several smaller cycles hidden by deferred imports. There is also lifted resource-loading surface area, especially `kind`/binary handling, that currently advertises capability the framework immediately rejects.

### Findings
1. **Runtime composition is still a passive dataclass plus sibling functions**
   - **Lens**: A
   - **Location**: `src/angee/base/compose/emission.py:25`, `src/angee/base/compose/pipeline.py:44`
   - **Severity**: High
   - **Problem**: `RuntimePlan` only holds `addons`, `extensions`, and `labels`, while `plan_runtime`, `check_runtime`, `reset_runtime_dir`, `emit_runtime_sources`, `import_runtime_models`, `normalize_migration_headers`, `emit_schema_sdl`, `check_schema_sdl`, `pipeline.run`, and `clean_runtime` mutate or emit from that same plan. This violates `docs/backend/guidelines.md`'s rule to compose behavior onto the class that owns the data.
   - **Recommendation**: Replace `RuntimePlan` and the orchestration functions with an `AngeeRuntime` class that owns discovery, planning, source rendering, checking, reset, import, SDL, migration normalization, permission writing, build, and clean.

2. **`.angee-manifest.json` is still generated and used as the runtime safety marker**
   - **Lens**: D
   - **Location**: `src/angee/base/compose/emission.py:83`, `src/angee/base/compose/emission.py:107`, `src/angee/base/compose/pipeline.py:94`, `src/angee/base/compose/emission.py:503`
   - **Severity**: High
   - **Problem**: The decided removal has not happened: emit writes `.angee-manifest.json`, reset and clean refuse to delete without it, and `_is_checked_runtime_source` includes it in drift checks. This keeps a second source of truth for addons/resources/runtime apps instead of trusting the explicit `ANGEE_RUNTIME_DIR`.
   - **Recommendation**: Delete the manifest write and `_resource_manifest`; make check compare rendered source strings against disk without the manifest; ground reset/clean in the configured `AngeeRuntime.runtime_dir` and, where possible, delete only planned generated paths. No content marker is needed if the host passes the explicit runtime directory.

3. **The base `Resource` model ownership is split by a bottom re-export cycle**
   - **Lens**: B
   - **Location**: `src/angee/base/models.py:126`, `src/angee/base/resources/models.py:8`, `src/angee/base/apps.py:118`
   - **Severity**: High
   - **Problem**: The base addon’s conventional `models.py` re-exports `Resource` from `resources.models` after defining `AngeeModel`; `resources.models` imports `AngeeModel` back from `models.py`. That cycle forces deferred imports in `apps.py` and violates the imports-at-top rule and the Django-native “source models live in `models.py`” convention.
   - **Recommendation**: Move the `Resource` source model to the owning conventional module, `angee.base.models`, or make a single explicit owner without a bottom re-export. Move tier normalization to a neutral resource primitive if needed, update imports, and delete the `models.py` late import and unnecessary `__all__`.

4. **Resource loader helpers are methods waiting to happen**
   - **Lens**: A
   - **Location**: `src/angee/base/resources/loader.py:97`, `src/angee/base/resources/loader.py:117`, `src/angee/base/resources/loader.py:314`, `src/angee/base/resources/loader.py:357`, `src/angee/base/resources/loader.py:387`
   - **Severity**: Medium
   - **Problem**: `_row_xref`, `_row_content_hash`, `_adopt_existing_target`, and `_restore_auto_fields` all operate on `AngeeResource` state (`entry`, `_meta`, `fields`, ledger model) from outside the class. That is the “function takes an object and inspects it” smell from `AGENTS.md`.
   - **Recommendation**: Move those helpers onto `AngeeResource` as private methods. Consider making `build_resource` a classmethod/factory on `AngeeResource` so the manager asks the resource owner directly.

5. **GraphQL schema composition has a passive dict-shaped owner**
   - **Lens**: A
   - **Location**: `src/angee/base/graphql/schema.py:37`, `src/angee/base/graphql/schema.py:54`, `src/angee/base/graphql/schema.py:62`, `src/angee/base/graphql/schema.py:94`
   - **Severity**: Medium
   - **Problem**: `SchemaParts` is a dict alias passed through `collect_schema_parts`, `collect_schema_names`, `build_schema`, and `render_sdl`. The behavior repeatedly rediscovers addons and reinterprets the same shape from outside.
   - **Recommendation**: Introduce a small schema owner, e.g. `GraphQLSchemas.from_addons(addons)`, with `names()`, `build(name)`, and `render_sdl()` methods. Keep module-level functions only as thin compatibility wrappers if needed.

6. **Resource `kind` / binary handling is unearned surface area**
   - **Lens**: C
   - **Location**: `src/angee/base/apps.py:114`, `src/angee/base/resources/entries.py:52`, `src/angee/base/resources/entries.py:73`, `src/angee/base/resources/managers.py:121`, `src/angee/base/resources/managers.py:146`
   - **Severity**: Medium
   - **Problem**: The manifest accepts `kind`, `entries.py` classifies binary formats, and `diff_addons` pretends binary entries exist, but loading immediately raises “binary resources are not implemented yet.” This is speculative generality, not a supported framework contract.
   - **Recommendation**: Delete `kind`, `BINARY_FORMATS`, binary classification, the `diff_addons` binary special case, and the binary-resource test until binary resource ownership is actually implemented.

7. **Deferred imports remain across app startup, resources, parsing, and reversion**
   - **Lens**: B
   - **Location**: `src/angee/base/apps.py:240`, `src/angee/base/apps.py:375`, `src/angee/base/apps.py:395`, `src/angee/base/mixins.py:88`, `src/angee/base/signals.py:31`, `src/angee/base/resources/managers.py:40`
   - **Severity**: Medium
   - **Problem**: These imports are not optional dependencies; they are hiding app/model/resource cycles or old app-loading caution. The updated backend rule says deferred imports are architecture smells to fix at the seam.
   - **Recommendation**: Break the model/resource cycle first, then move imports to module top. For `fetch_url`, move `ResourceLoadError` to a neutral `resources/exceptions.py` or invert fetch error handling so `entries.py` can import `fetch_url` normally.

### Proposed `AngeeRuntime` shape
`AngeeRuntime` should own the runtime directory, data directory, runtime module, addons, extension grouping, and emitted labels. It should have constructors like `from_settings(addons=None)` and `from_addons(addons, runtime_dir, data_dir, runtime_module)`, then methods `check()`, `reset()`, `render_sources()`, `emit_sources()`, `import_models()`, `render_schema_sdl()`, `emit_schema_sdl()`, `check_schema_sdl()`, `normalize_migration_headers()`, `build(apply, check=False)`, and `clean()`.

`RuntimePlan` should disappear or become private cached state inside `AngeeRuntime`; it should not be a public object passed through sibling functions. `pipeline.run` should become a thin command-facing wrapper around `AngeeRuntime.from_settings(addons).build(...)`, and `clean_runtime` should delegate to `AngeeRuntime.from_settings().clean()`. The emission module should either become private methods on `AngeeRuntime` or a narrow renderer owned by it; source rendering should return a deterministic `{relative_path: text}` map so `--check` can diff rendered strings against disk without a manifest.

### Inline-import inventory
| Location | Import | Reason | Fix |
|---|---|---|---|
| `src/angee/base/apps.py:118` | `from angee.base.resources.models import Resource` | Deferred to avoid the `apps.py` / `models.py` / `resources.models` cycle while reading `Resource.Tier`. | Move `Resource` or tier normalization to a single owner importable at module top. |
| `src/angee/base/apps.py:240` | `from angee.base.models import AngeeModel` | Deferred inside source model scanning to avoid importing `models.py` while apps are loading. | Break the resource/model cycle, then import `AngeeModel` at top or move the predicate to the model owner. |
| `src/angee/base/apps.py:375` | `from angee.base.models import AngeeModel` | Same cycle as above. | Same fix as above. |
| `src/angee/base/apps.py:395` | `from angee.base.signals import register_revision_models` | App-ready caution, not an optional dependency. | Import at module top after confirming no side effects run at import time. |
| `src/angee/base/mixins.py:88` | `import reversion` | Deferred inside a property, but `django-reversion` is a locked stack dependency. | Move to module top. |
| `src/angee/base/models.py:126` | `from angee.base.resources.models import Resource` | Bottom re-export so the composer discovers `Resource` through `angee.base.models`. | Make `Resource` live in the conventional source model module or give it one explicit owner; delete the re-export. |
| `src/angee/base/resources/entries.py:17` | `from angee.base.apps import BaseAddonConfig` under `TYPE_CHECKING` | Type-only workaround for resource/app cycles. | Use `AppConfig`/a neutral protocol or break the cycle and import normally. |
| `src/angee/base/resources/entries.py:136` | `from angee.base.resources.fetch import fetch_url` | Avoids `entries.py` ↔ `fetch.py` because `fetch.py` imports `ResourceLoadError` from `entries.py`. | Move `ResourceLoadError` to `resources.exceptions` or invert fetch error wrapping, then top-import `fetch_url`. |
| `src/angee/base/resources/entries.py:203` | `import json` | No cycle; unnecessary function-local stdlib import. | Move to module top. |
| `src/angee/base/resources/entries.py:212` | `import yaml` | No optional dependency; PyYAML is locked. | Move to module top. |
| `src/angee/base/resources/loader.py:29` | `from angee.base.resources.models import Resource` under `TYPE_CHECKING` | Type-only workaround for loader/resource model cycle. | Drop the concrete `Resource` annotation or use a neutral protocol/model type. |
| `src/angee/base/resources/managers.py:26` | `from angee.base.apps import BaseAddonConfig` under `TYPE_CHECKING` | Type-only workaround for manager/app cycles. | Use a neutral protocol or top import after cycle cleanup. |
| `src/angee/base/resources/managers.py:40` | `from angee.base.resources.loader import build_resource` | Deferred to avoid loader/model cycles. | Move resource factory ownership onto `AngeeResource` and import at top after cycle cleanup. |
| `src/angee/base/resources/managers.py:68` | `DryRunRollback`, `build_resource`, `result_counts` | Same loader cycle as above. | Same fix as above. |
| `src/angee/base/resources/managers.py:170` | `from angee.base.discovery import discover_addons` | Manager reaches into composition discovery, creating a layer cycle risk. | Let commands/runtime composition discover addons and pass them in, or break the cycle and top-import. |
| `src/angee/base/signals.py:31` | `import reversion` | Not optional; stack dependency. | Move to module top. |
| `src/angee/base/signals.py:32` | `from django.apps import apps` | No real cycle; app registry is only read when function runs. | Move to module top. |

### Top recommendations
1. Build `AngeeRuntime` first and move all runtime plan/emit/check/reset/clean behavior onto it.
2. Remove `.angee-manifest.json` completely and make `--check` compare rendered source maps plus rendered SDL against disk.
3. Break the `models.py` / `resources.models` ownership cycle by giving `Resource` and resource tiers one explicit owner.
4. Move every deferred import to module top after the ownership fixes, and delete the resource `kind`/binary surface until it is real.
