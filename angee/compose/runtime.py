"""Compose generated sources and own their guarded filesystem lifecycle."""

from __future__ import annotations

import importlib
import logging
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

from django.apps import AppConfig, apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db.migrations.loader import MigrationLoader

from angee.compose.declarations import BuildIntegration, model_declaration
from angee.compose.migrations import RuntimeMigrations
from angee.compose.model_composition import ModelComposition
from angee.compose.rendering import render_models
from angee.utils.artifacts import GeneratedTree

GENERATED_SENTINEL = "# ANGEE GENERATED RUNTIME - DO NOT EDIT"
"""Marker required before the configured generated runtime may be changed."""

logger = logging.getLogger(__name__)


def _runtime_directory() -> Path:
    """Read the single runtime-directory setting without discovering models."""

    runtime_dir = getattr(settings, "COMPOSE_RUNTIME_DIR", None)
    if not runtime_dir:
        raise ImproperlyConfigured(
            "angee.compose requires COMPOSE_RUNTIME_DIR; "
            "the host must configure the generated runtime directory."
        )
    return Path(runtime_dir)


def _generated_tree(
    runtime_dir: Path, sources: Mapping[Path, str], *, clean: bool = False,
) -> GeneratedTree:
    """Scope normal reconciliation and explicit cleanup without owning history."""

    source_roots = {path.parts[0] for path in sources}
    external_roots = frozenset(getattr(settings, "COMPOSE_EXTERNAL_ARTIFACT_ROOTS", ()))
    owned_paths = frozenset(Path(path) for path in getattr(settings, "COMPOSE_OWNED_ARTIFACT_PATHS", ()))

    def owns(path: Path) -> bool:
        root = path.parts[0]
        if "migrations" in path.parts:
            return False
        if clean:
            return True
        return (
            (root not in external_roots or path in sources or path in owned_paths)
            # Current packages accumulate bytecode on import; retired ones must
            # still be pruned when only their bytecode remains.
            and ("__pycache__" not in path.parts or root not in source_roots | {"__pycache__"})
        )

    configured = getattr(settings, "COMPOSE_RUNTIME_DIR", None)
    return GeneratedTree(
        runtime_dir,
        sources,
        owns=owns,
        sentinel=(Path("__init__.py"), GENERATED_SENTINEL),
        clean_root=Path(configured) if configured is not None else None,
    )


class Runtime:
    """Coordinate native composition and installed build integrations.

    ``discover`` imports and validates source declarations explicitly. Each write
    or drift operation renders one source map. Explicit build uses guarded
    reconciliation. Neither construction nor discovery changes settings or files.
    """

    def __init__(
        self,
        addons: tuple[AppConfig, ...],
        composition: ModelComposition,
        *,
        runtime_dir: Path,
        runtime_module: str = "runtime",
        integrations: tuple[BuildIntegration, ...] | None = None,
    ) -> None:
        self.addons = addons
        self.composition = composition
        self.runtime_dir = runtime_dir
        self.runtime_module = runtime_module
        self.integrations = BuildIntegration.configured() if integrations is None else integrations
        self.model_declarations = {
            source: model_declaration(composition, source, self.integrations)
            for source in composition.ordered_models
        }

    @classmethod
    def discover(
        cls,
        addons: Iterable[AppConfig],
        *,
        runtime_dir: Path,
        runtime_module: str = "runtime",
        integrations: tuple[BuildIntegration, ...] | None = None,
    ) -> Runtime:
        """Discover source models after Django has populated AppConfig instances."""

        configs = tuple(addons)
        return cls(
            configs,
            ModelComposition.discover(configs),
            runtime_dir=runtime_dir,
            runtime_module=runtime_module,
            integrations=integrations,
        )

    @classmethod
    def from_django(cls) -> Runtime:
        """Discover installed sources without mutating settings or generated files."""

        return cls.discover(
            apps.get_app_configs(),
            runtime_dir=_runtime_directory(),
            runtime_module=str(getattr(settings, "COMPOSE_RUNTIME_MODULE", "runtime")),
        )

    @classmethod
    def clean_configured(cls) -> None:
        """Clean generated packages without discovery, leaving migrations for explicit reset."""

        runtime_module = str(getattr(settings, "COMPOSE_RUNTIME_MODULE", "runtime"))
        runtime = sys.modules.get(runtime_module)
        runtime_dir = _runtime_directory()
        retained = _generated_tree(runtime_dir, {}, clean=True).clean()
        current_roots = set(getattr(runtime, "RUNTIME_APPS", ()))
        retired = sorted({
            Path(*path.parts[:path.parts.index("migrations") + 1])
            for path in retained
            if "migrations" in path.parts and path.parts[0] not in current_roots
        })
        if retired:
            logger.warning(
                "Preserved migration directories during cleanup: %s. "
                "Review their database history before an explicitly authorized removal.",
                ", ".join(str(runtime_dir / path) for path in retired),
            )

    @property
    def labels(self) -> tuple[str, ...]:
        """Return app labels owned by the native model composition."""

        return self.composition.labels

    def integration_modules(self) -> tuple[str, ...]:
        """Collect explicit emitted imports without a serving-time build callback."""

        modules = tuple(name for integration in self.integrations for name in integration.runtime_imports(self))
        if len(set(modules)) != len(modules):
            raise ImproperlyConfigured("Build integrations declare duplicate runtime imports")
        return modules

    def render_sources(self) -> dict[Path, str]:
        """Render concrete models and installed integrations into one source map."""

        model_modules = tuple(dict.fromkeys(
            f"{self.runtime_module}.{source._meta.app_label}.models"
            for source in self.composition.ordered_models
        ))
        migration_modules = {label: MigrationLoader.migrations_module(label)[0] for label in self.labels}
        app_identities = tuple((config.name, config.label) for config in self.addons)
        sources: dict[Path, str] = {
            Path("__init__.py"): (
                f'"""Generated Angee runtime package."""\n'
                f"{GENERATED_SENTINEL}\n\n"
                f"RUNTIME_APPS = {list(self.labels)!r}\n"
                f"RUNTIME_APP_IDENTITIES = {app_identities!r}\n"
                f"RUNTIME_MODEL_MODULES = {model_modules!r}\n"
                f"RUNTIME_INTEGRATION_MODULES = {self.integration_modules()!r}\n"
                f"RUNTIME_MIGRATION_MODULES = {migration_modules!r}\n"
            ),
        }
        for label in self.labels:
            root = Path(label)
            sources[root / "__init__.py"] = ""
            sources[root / "models.py"] = render_models(
                self.composition, label, runtime_module=self.runtime_module, declarations=self.model_declarations,
            )
        for integration in self.integrations:
            contributed = integration.render_sources(self)
            self._validate_artifacts(contributed)
            for path, source in sorted(contributed.items()):
                if path in sources or any(parent in sources for parent in path.parents) or any(
                    path in existing.parents for existing in sources
                ):
                    raise ImproperlyConfigured(f"Competing build artifacts at {path}")
                sources[path] = source
        for module in self.integration_modules():
            prefix = f"{self.runtime_module}."
            if not module.startswith(prefix):
                raise ImproperlyConfigured(f"Generated import is outside runtime: {module!r}")
            relative = Path(*module.removeprefix(prefix).split(".")).with_suffix(".py")
            if relative not in sources:
                raise ImproperlyConfigured(f"Generated import has no emitted source: {module!r}")
        self._validate_artifacts(sources)
        return sources

    def _validate_artifacts(self, sources: Mapping[Path, str]) -> None:
        """Translate the shared artifact owner's failure into a settings error."""

        try:
            GeneratedTree.validate_artifacts(self.runtime_dir, sources)
        except ValueError as error:
            raise ImproperlyConfigured(str(error)) from error

    def runtime_migrations(self) -> RuntimeMigrations:
        """Return the native addon migration materializer."""

        return RuntimeMigrations(
            self.addons,
            labels=self.labels,
        )

    def emit_sources(self) -> bool:
        """Emit explicit build output without executing native migration operations."""

        tree = _generated_tree(self.runtime_dir, self.render_sources())
        changed = tree.reconcile(prune=True)
        return changed

    def import_generated_models(self) -> None:
        """Import concrete modules in parent order; Django runs native class validation."""

        importlib.invalidate_caches()
        imported: set[str] = set()
        for source in self.composition.ordered_models:
            label = source._meta.app_label
            if label not in imported:
                importlib.import_module(f"{self.runtime_module}.{label}.models")
                imported.add(label)
        for module in self.integration_modules():
            importlib.import_module(module)

    def check(self) -> None:
        """Raise for generated-source or declared-migration drift."""

        drift = _generated_tree(self.runtime_dir, self.render_sources()).drift()
        if drift:
            rendered = ", ".join(str(path) for path in drift)
            raise RuntimeError(f"generated runtime is stale: {rendered}")
        self.runtime_migrations().check()
