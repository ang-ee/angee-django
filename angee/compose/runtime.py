"""Compose generated sources and own their guarded filesystem lifecycle."""

from __future__ import annotations

import importlib
from collections.abc import Iterable, Mapping
from pathlib import Path

from django.apps import AppConfig, apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from angee.compose.dependencies import AddonDependencyGroup, AddonDependencyGroupResult
from angee.compose.migrations import RuntimeMigrations
from angee.compose.model_composition import ModelComposition
from angee.compose.permissions import apply_schema_paths, extension_source_map
from angee.compose.rendering import render_models
from angee.compose.web import WebRuntime
from angee.fs import GENERATED_SENTINEL, GeneratedTree
from angee.project import find_project_dir

_COMPOSER_WEB_SOURCES = {
    Path("web/manifest.json"),
    Path("web/tailwind.sources.css"),
}


def _is_runtime_source(path: Path) -> bool:
    """Scope drift/pruning; explicit cleanup preserves only migrations."""

    root = path.parts[0] if path.parts else ""
    return (
        root not in {"gql", "schemas"}
        and (root != "web" or path in _COMPOSER_WEB_SOURCES)
        and "__pycache__" not in path.parts
    )


def _runtime_directory() -> Path:
    """Read the single runtime-directory setting without discovering models."""

    runtime_dir = getattr(settings, "ANGEE_RUNTIME_DIR", None)
    if not runtime_dir:
        raise ImproperlyConfigured(
            "angee.compose requires ANGEE_RUNTIME_DIR; angee.compose.settings "
            "sets it. A host installing the composer must configure the runtime directory."
        )
    return Path(runtime_dir)


def _generated_tree(runtime_dir: Path, sources: Mapping[Path, str]) -> GeneratedTree:
    """Apply one runtime policy to rendered sources or discovery-free cleanup."""

    configured = getattr(settings, "ANGEE_RUNTIME_DIR", None)
    return GeneratedTree(
        runtime_dir,
        sources,
        owns=_is_runtime_source,
        sentinel=(Path("__init__.py"), GENERATED_SENTINEL),
        clean_root=Path(configured) if configured is not None else None,
    )


class Runtime:
    """Coordinate model, web, permission, dependency and migration owners.

    ``discover`` imports and validates source declarations explicitly. Each write
    or drift operation renders one source map. Boot repairs sources without
    pruning; explicit build retains the guarded reset policy. Neither construction
    nor discovery changes Django settings or writes files.
    """

    def __init__(
        self,
        addons: tuple[AppConfig, ...],
        composition: ModelComposition,
        *,
        runtime_dir: Path,
        runtime_module: str = "runtime",
        project_dir: Path | None = None,
    ) -> None:
        self.addons = addons
        self.composition = composition
        self.runtime_dir = runtime_dir
        self.runtime_module = runtime_module
        self.project_dir = project_dir

    @classmethod
    def discover(
        cls,
        addons: Iterable[AppConfig],
        *,
        runtime_dir: Path,
        runtime_module: str = "runtime",
        project_dir: Path | None = None,
    ) -> Runtime:
        """Discover source models after Django has populated AppConfig instances."""

        configs = tuple(addons)
        return cls(
            configs,
            ModelComposition.discover(configs),
            runtime_dir=runtime_dir,
            runtime_module=runtime_module,
            project_dir=project_dir,
        )

    @classmethod
    def from_django(cls) -> Runtime:
        """Discover installed sources without mutating settings or generated files."""

        return cls.discover(
            apps.get_app_configs(),
            runtime_dir=_runtime_directory(),
            runtime_module=str(getattr(settings, "ANGEE_RUNTIME_MODULE", "runtime")),
            project_dir=find_project_dir(),
        )

    @classmethod
    def clean_configured(cls) -> None:
        """Clean the configured tree without importing or rendering addon sources."""

        _generated_tree(_runtime_directory(), {}).clean()

    @property
    def labels(self) -> tuple[str, ...]:
        """Return app labels owned by the native model composition."""

        return self.composition.labels

    def render_sources(self) -> dict[Path, str]:
        """Render one coherent model/web/permission source map before any write."""

        sources: dict[Path, str] = {
            Path("__init__.py"): (
                f'"""Generated Angee runtime package."""\n'
                f"{GENERATED_SENTINEL}\n\n"
                f"RUNTIME_APPS = {list(self.labels)!r}\n"
            ),
        }
        for label in self.labels:
            root = Path(label)
            sources[root / "__init__.py"] = ""
            sources[root / "migrations" / "__init__.py"] = ""
            sources[root / "models.py"] = render_models(
                self.composition, label, runtime_module=self.runtime_module,
            )
        sources.update(WebRuntime(self.addons, runtime_dir=self.runtime_dir).render_sources())
        sources.update(extension_source_map(self.addons))
        return sources

    def emit(self) -> None:
        """Reset behind the cleanup gate, then write one rendered source map."""

        self._emit(_generated_tree(self.runtime_dir, self.render_sources()))

    def _emit(self, tree: GeneratedTree) -> None:
        tree.reset()
        tree.reconcile(prune=True)
        apply_schema_paths(self.addons, self.runtime_dir, sources=tree.artifacts)

    def runtime_migrations(self) -> RuntimeMigrations:
        """Return the native addon migration materializer."""

        return RuntimeMigrations(self.addons, runtime_dir=self.runtime_dir, labels=self.labels)

    @property
    def addon_dependency_group(self) -> AddonDependencyGroup:
        """Return the dependency projector for this composed host."""

        return AddonDependencyGroup.from_app_configs(self.addons, project_dir=self.project_dir)

    def build(self) -> AddonDependencyGroupResult:
        """Repair sources, project dependencies and materialize addon migrations."""

        tree = _generated_tree(self.runtime_dir, self.render_sources())
        if tree.drift():
            self._emit(tree)
        dependency_result = self.addon_dependency_group.write()
        self.runtime_migrations().materialize()
        return dependency_result

    def import_generated_models(self) -> None:
        """Import concrete modules in model-parent order and validate final metadata."""

        importlib.invalidate_caches()
        imported: set[str] = set()
        for source in self.composition.ordered_models:
            label = source._meta.app_label
            if label not in imported:
                importlib.import_module(f"{self.runtime_module}.{label}.models")
                imported.add(label)
        self.composition.validate_concrete(
            apps.get_model(source._meta.label_lower, require_ready=False)
            for source in self.composition.ordered_models
        )

    def emit_if_stale(self) -> bool:
        """Repair boot sources without pruning, then bind effective permission paths."""

        sources = self.render_sources()
        changed = _generated_tree(self.runtime_dir, sources).reconcile(prune=False)
        apply_schema_paths(self.addons, self.runtime_dir, sources=sources)
        return changed

    def configure_migration_modules(self) -> None:
        """Bind generated migrations explicitly during Django population phase 2.

        Explicit None disables Django migrations and therefore conflicts with an
        emitted app's composer-owned migration module, just like another path.
        """

        migration_modules = dict(getattr(settings, "MIGRATION_MODULES", {}))
        for label in self.labels:
            module = f"{self.runtime_module}.{label}.migrations"
            if label in migration_modules and migration_modules[label] != module:
                raise ImproperlyConfigured(f"Project settings define Runtime-owned MIGRATION_MODULES[{label!r}]")
            migration_modules[label] = module
        settings.MIGRATION_MODULES = migration_modules

    def is_current(self) -> bool:
        """Compare disk with one freshly rendered source map."""

        return not _generated_tree(self.runtime_dir, self.render_sources()).drift()

    def check(self) -> None:
        """Raise for source, dependency-group or migration drift."""

        drift = _generated_tree(self.runtime_dir, self.render_sources()).drift()
        if drift:
            rendered = ", ".join(str(path) for path in drift)
            raise RuntimeError(f"generated runtime is stale: {rendered}")
        self.addon_dependency_group.check()
        self.runtime_migrations().check()

    def reset(self) -> None:
        """Clear generated output behind the guard, preserving migrations."""

        _generated_tree(self.runtime_dir, {}).reset()

    def clean(self) -> None:
        """Delete generated output without rendering, preserving migrations."""

        _generated_tree(self.runtime_dir, {}).clean()
