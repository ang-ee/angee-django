### Summary
The `angee.base` core exhibits strong foundational patterns but suffers from a primary structural issue: behavior is often decoupled from the data it operates on, most critically in the build pipeline. This manifests as loose functions operating on passive dataclasses, a pattern that should be refactored into a cohesive `AngeeRuntime` class. This review also identifies several non-top-level imports that mask underlying architectural problems like import cycles. Finally, there are clear opportunities for simplification by removing the now-decided-against `.angee-manifest.json` and deleting speculative "unearned" code.

### Findings

1.  **Title**: Build Pipeline Logic is a Set of Loose Functions Around a Passive Dataclass
    -   **Lens**: A
    -   **Location**: `src/angee/base/compose/emission.py`, `src/angee/base/compose/pipeline.py`
    -   **Severity**: Critical
    -   **Problem**: The `RuntimePlan` dataclass in `emission.py` is a passive data container manipulated by over a dozen loose functions (`plan_runtime`, `emit_runtime_sources`, `check_runtime`, etc.) spread across `emission.py` and `pipeline.py`. This violates the "compose behavior onto the class that owns the data" guideline. The logic for building, checking, and cleaning the runtime is scattered and has no single owner.
    -   **Recommendation**: Create a new `AngeeRuntime` class. `RuntimePlan`'s fields should be moved into `AngeeRuntime`'s `__init__`. The various functions (`plan_runtime`, `emit_runtime_sources`, `check_runtime`, `reset_runtime_dir`, etc.) should become methods on this class. The `pipeline.run` function would then instantiate `AngeeRuntime` and call its methods.

2.  **Title**: Circular Dependency Between `angee.base.models` and `angee.base.resources.models`
    -   **Lens**: B
    -   **Location**: `src/angee/base/models.py:111`
    -   **Severity**: High
    -   **Problem**: `angee.base.models.py` imports `Resource` from `angee.base.resources.models` at the bottom of the file to avoid a circular import. This is necessary because `resources.models.Resource` inherits from `angee.base.models.AngeeModel`. This local import hides a structural flaw, violating the "imports go at the top of the module" rule.
    -   **Recommendation**: Break the cycle. The `Resource` model is fundamental to the `angee.base` addon. It should be defined directly in `angee.base.models.py`. The related manager (`ResourceManager`) and other resource-specific logic in the `resources` subpackage can import it from there, eliminating the cycle and the need for a non-top-level import.

3.  **Title**: Safety Guards for Directory Cleaning are Tied to the `.angee-manifest.json`
    -   **Lens**: D
    -   **Location**: `src/angee/base/compose/emission.py:73`, `src/angee/base/compose/pipeline.py:101`
    -   **Severity**: High
    -   **Problem**: `reset_runtime_dir` and `clean_runtime` use the existence of `.angee-manifest.json` as a safety check before deleting contents of the runtime directory. With the decision to remove this file, these safety checks will fail or become ineffective, creating a risk of deleting files in an incorrect directory.
    -   **Recommendation**: Replace the manifest check with a more robust and direct guard. Since the `runtime_dir` path is passed explicitly, a simple and effective check would be to verify that the directory's basename is `runtime` and that it contains a file indicating it is a generated Angee directory, such as the `__init__.py` that the build process creates. For example: `if not (runtime_dir.name == 'runtime' and (runtime_dir / '__init__.py').exists()): raise RuntimeError(...)`.

4.  **Title**: Scattered Non-Top-Level Imports Obscure Dependencies
    -   **Lens**: B
    -   **Location**: Multiple files (see inventory below)
    -   **Severity**: Medium
    -   **Problem**: Several modules use function-local imports to resolve dependencies. This is an architecture smell that hides potential import cycles or layering violations, making the codebase harder to understand and maintain.
    -   **Recommendation**: Move all imports to the top of their respective modules. Each case should be analyzed and the underlying dependency issue fixed. For instance, the local imports in `angee.base.resources.managers.py` indicate that the manager has too many responsibilities and might be pulling from modules that should not be its direct dependencies.

5.  **Title**: Speculative Code for Adopting Un-Ledgered Database Rows
    -   **Lens**: C
    -   **Location**: `src/angee/base/resources/loader.py:270`
    -   **Severity**: Medium
    -   **Problem**: The `_adopt_existing_target` function in the resource loader attempts to find an existing database row by matching on a unique field if a ledger entry is missing. This is "speculative generality" and "dead defensiveness." It can lead to incorrect data linking if the unique constraint is not as globally unique as assumed (e.g., across different environments or tenants). It adds complexity for a feature that is not clearly required and could be dangerous.
    -   **Recommendation**: Delete the `_adopt_existing_target` function and the logic in `import_row` that calls it. The resource loading process should be explicit. If a resource row has no ledger entry, it should be treated as a new entry. If a unique constraint is violated during creation, the database will raise an `IntegrityError`, which is a clearer and more standard failure mode.

6.  **Title**: `.angee-manifest.json` is Generated But No Longer Needed
    -   **Lens**: D
    -   **Location**: `src/angee/base/compose/emission.py:91`
    -   **Severity**: Low
    -   **Problem**: The `emit_runtime_sources` function writes an `.angee-manifest.json` file. The team has decided to remove this file, making its generation dead code. The drift check (`check_runtime`) will correctly identify the manifest as drift if it's removed from the generator but present on disk.
    -   **Recommendation**: Remove the code block responsible for writing `.angee-manifest.json` from `emit_runtime_sources`. The `reset_runtime_dir` function, when corrected as per finding #3, will handle the cleanup of any old manifest files from the runtime directory during a build.

### Proposed `AngeeRuntime` shape

The `compose/emission.py` and `compose/pipeline.py` modules should be refactored into a single `AngeeRuntime` class, likely living in a new `src/angee/base/compose/runtime.py` file.

```python
# src/angee/base/compose/runtime.py

from __future__ import annotations
from pathlib import Path
from angee.base.apps import BaseAddonConfig

class AngeeRuntime:
    """
    Owns the build-time composition of a deterministic runtime
    from a set of discovered addons.
    """
    
    runtime_dir: Path
    addons: tuple[BaseAddonConfig, ...]
    extensions: dict[str, tuple[type, ...]]
    labels: list[str]

    def __init__(self, runtime_dir: Path, addons: tuple[BaseAddonConfig, ...]):
        self.runtime_dir = runtime_dir
        self.addons = addons
        
        # Logic from plan_runtime
        self.extensions = self._plan_extensions()
        self._check_field_collisions()
        self.labels = self._plan_labels()

    def build(self, apply: bool = True) -> BuildResult:
        """Main entry point to build the runtime."""
        self._reset_dir()
        self._emit_sources()
        self._import_models()
        self._emit_schema_sdl()

        if self.labels:
            # call_command("makemigrations", ...)
            self._normalize_migration_headers()
        
        if apply:
            # call_command("migrate", ...)
            # sync_permissions()
        
        return BuildResult(...)

    def check(self) -> None:
        """Check for drift in generated sources."""
        # Logic from check_runtime and check_schema_sdl
        ...

    def clean(self) -> None:
        """Clean generated runtime files."""
        # Logic from clean_runtime
        ...

    def _reset_dir(self) -> None:
        # Logic from reset_runtime_dir
        ...

    def _emit_sources(self) -> None:
        # Logic from emit_runtime_sources
        ...
    
    # ... other private methods encapsulating emission logic ...
```
`RuntimePlan` would be deleted. `compose/pipeline.run` would become a thin wrapper that instantiates `AngeeRuntime` and calls `build()` or `check()`.

### Inline-import inventory

| File | Location | Import | Reason & Recommendation |
| --- | --- | --- | --- |
| `src/angee/base/models.py` | Line 111 (bottom) | `from angee.base.resources.models import Resource` | **Import Cycle**: `resources.models.Resource` subclasses `AngeeModel` from this file. **Fix**: Move the `Resource` source model definition into `angee.base.models.py` itself to break the cycle. |
| `src/angee/base/apps.py` | `resource_manifest()` | `from angee.base.resources.models import Resource` | **Django App Loading**: Likely deferred to ensure the model is available. **Fix**: Moving the `Resource` model (see above) would make this importable at the top level. |
| `src/angee/base/apps.py` | `_is_source_model()` | `from angee.base.models import AngeeModel` | **Import Cycle**: `apps.py` is often imported early. **Fix**: The cycle with `models.py` is common in Django; moving the import to the top might work if other dependencies are resolved correctly. If not, refactoring the check may be needed. |
| `src/angee/base/apps.py` | `ready()` | `from angee.base.signals import register_revision_models` | **Django App Loading**: Called during app initialization. This is a standard Django pattern and acceptable. |
| `src/angee/base/resources/managers.py` | `validate_addons()`, `load_addons()`, `_entries_for()` | `build_resource`, `discover_addons`, etc. | **Layering Violation**: The manager is reaching into `loader` and `discovery`. This suggests the manager has too many responsibilities. **Fix**: Refactor this logic into a service/class that orchestrates loading, which can be imported at the top level. |
| `src/angee/base/mixins.py` | `revisions()` | `import reversion` | **Optional Dependency**: The import is inside a method. **Fix**: Since `django-reversion` is a core dependency (`stack.md`), this import should be at the top level. |
| `src/angee/base/signals.py` | `register_revision_models()` | `import reversion`, `from django.apps import apps` | **Django App Loading**: This function is called from `apps.ready()`. This is standard Django practice. |
| `src/angee/base/views.py` | `_get_view()` | `from angee.base.graphql.schema import build_schema` | **Lazy Loading**: Defers schema building until the first request. This is an acceptable performance optimization. |
| `src/angee/base/resources/loader.py` | `materialize()` | `from angee.base.resources.fetch import fetch_url` | **Import Cycle/Layering**: `fetch` may have dependencies that make a top-level import problematic. **Fix**: Should be moved to the top. If there's a cycle, it needs to be broken. |

### Top recommendations
1.  Refactor the build process into a dedicated `AngeeRuntime` class to properly own its state and behavior.
2.  Eliminate the `models` <-> `resources` circular dependency by moving the `Resource` model definition to `angee.base.models.py`.
3.  Replace the `.angee-manifest.json`-based safety guards in `reset_runtime_dir` and `clean_runtime` with a check against the directory name and presence of a generated `__init__.py`.
4.  Delete the speculative `_adopt_existing_target` logic in the resource loader to prevent potential data corruption and reduce complexity.
5.  Move all function-local imports to the top of their modules, fixing the underlying architectural issues they hide.
