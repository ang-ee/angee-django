"""Compose generated sources and own their guarded filesystem lifecycle."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

from django.apps import AppConfig, apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import DEFAULT_DB_ALIAS, connections, transaction
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.db.migrations.state import ProjectState

from angee.compose.declarations import BuildIntegration, model_declaration
from angee.compose.migrations import RuntimeMigrations
from angee.compose.model_composition import ModelComposition
from angee.compose.rendering import render_models
from angee.utils.artifacts import GENERATED_SENTINEL, GeneratedTree
from angee.utils.files import write_atomic


def _runtime_directory() -> Path:
    """Read the single runtime-directory setting without discovering models."""

    runtime_dir = getattr(settings, "COMPOSE_RUNTIME_DIR", None)
    if not runtime_dir:
        raise ImproperlyConfigured(
            "angee.compose requires COMPOSE_RUNTIME_DIR; "
            "the host must configure the generated runtime directory."
        )
    return Path(runtime_dir)


def _generated_tree(runtime_dir: Path, sources: Mapping[Path, str]) -> GeneratedTree:
    """Guard the whole runtime tree; sources never limit explicit cleanup."""

    source_roots = {path.parts[0] for path in sources}
    external_roots = frozenset(getattr(settings, "COMPOSE_EXTERNAL_ARTIFACT_ROOTS", ()))
    owned_paths = frozenset(Path(path) for path in getattr(settings, "COMPOSE_OWNED_ARTIFACT_PATHS", ()))

    def owns(path: Path) -> bool:
        root = path.parts[0]
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
    or drift operation renders one source map. Boot repairs sources without
    pruning; explicit build uses the guarded generated-tree policy. Neither construction
    nor discovery changes Django settings or writes files.
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
        _generated_tree(_runtime_directory(), {}).clean(current_roots=getattr(runtime, "RUNTIME_APPS", ()))

    @property
    def labels(self) -> tuple[str, ...]:
        """Return app labels owned by the native model composition."""

        return self.composition.labels

    def render_sources(self) -> dict[Path, str]:
        """Render concrete models and installed integrations into one source map."""

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
        self._validate_artifacts(sources)
        return sources

    def _validate_artifacts(self, sources: Mapping[Path, str]) -> None:
        """Translate the shared artifact owner's failure into a settings error."""

        try:
            GeneratedTree.validate_artifacts(self.runtime_dir, sources)
        except ValueError as error:
            raise ImproperlyConfigured(str(error)) from error

    def _emit(self, tree: GeneratedTree) -> None:
        tree.reset()
        tree.reconcile(prune=False)
        self._bind(tree.artifacts)

    def _bind(self, sources: Mapping[Path, str]) -> None:
        """Bind feature-owned paths only after the shared source map was emitted."""

        for integration in self.integrations:
            integration.bind(self, sources)

    def runtime_migrations(self) -> RuntimeMigrations:
        """Return the native addon migration materializer."""

        return RuntimeMigrations(
            self.addons,
            runtime_dir=self.runtime_dir,
            labels=self.labels,
        )

    def reset_migration_history(
        self,
        *,
        database: str = DEFAULT_DB_ALIAS,
    ) -> tuple[tuple[str, ...], int, int]:
        """Replace fully applied, model-clean composed history, restoring files on failure."""

        labels = tuple(sorted(self.labels))
        if not labels:
            return labels, 0, 0
        connection = connections[database]
        executor = MigrationExecutor(connection)
        executor.loader.check_consistent_history(connection)
        targets = tuple(node for label in labels for node in executor.loader.graph.leaf_nodes(label))
        if executor.migration_plan(targets):
            raise RuntimeError(
                "migration history reset requires a fully applied migration graph. "
                f"Run `manage.py migrate --database {database}` first."
            )
        changes = MigrationAutodetector(
            executor.loader.project_state(),
            ProjectState.from_apps(apps),
        ).changes(
            graph=executor.loader.graph,
            trim_to_apps=set(labels),
            convert_apps=set(labels),
        )
        if changes:
            rendered = " ".join(labels)
            raise RuntimeError(
                "migration history reset requires the recorded graph to match the live composed models. "
                f"Run `manage.py makemigrations {rendered}` and "
                f"`manage.py migrate --database {database}` first."
            )

        _generated_tree(self.runtime_dir, {}).reconcile(prune=False)
        migration_files = tuple(sorted(
            path
            for label in labels
            for path in (self.runtime_dir / label / "migrations").glob("*.py")
            if path.name != "__init__.py"
        ))
        original_sources = {path: path.read_bytes().decode("utf-8") for path in migration_files}

        migration_prefixes = tuple(f"{self.runtime_module}.{label}.migrations" for label in labels)

        def forget_migration_modules() -> None:
            for module_name in tuple(sys.modules):
                if any(
                    module_name == prefix or module_name.startswith(f"{prefix}.")
                    for prefix in migration_prefixes
                ):
                    sys.modules.pop(module_name)
            for label in labels:
                for bytecode in (self.runtime_dir / label / "migrations" / "__pycache__").glob("*.pyc"):
                    bytecode.unlink()
            importlib.invalidate_caches()

        try:
            for path in migration_files:
                path.unlink()
            forget_migration_modules()
            call_command(
                "makemigrations",
                *labels,
                interactive=False,
                include_header=False,
                verbosity=0,
            )
            forget_migration_modules()

            fresh_executor = MigrationExecutor(connection)
            graph = fresh_executor.loader.graph
            fresh_nodes = dict.fromkeys(
                node
                for label in labels
                for target in graph.leaf_nodes(label)
                for node in graph.forwards_plan(target)
                if node[0] in labels
            )
            recorder = MigrationRecorder(connection)
            with transaction.atomic(using=database):
                applied = recorder.applied_migrations()
                # Preserve shared keys: an applied external migration may depend
                # on one of these initials throughout the history replacement.
                for app_label, migration_name in fresh_nodes:
                    if (app_label, migration_name) not in applied:
                        recorder.record_applied(app_label, migration_name)
                for app_label, migration_name in sorted(applied.keys() - fresh_nodes.keys()):
                    if app_label in labels:
                        recorder.record_unapplied(app_label, migration_name)
                fresh_executor.loader.check_consistent_history(connection)
                recorded = recorder.migration_qs.filter(app__in=labels).count()
        except BaseException:
            for label in labels:
                for path in (self.runtime_dir / label / "migrations").glob("*.py"):
                    if path.name != "__init__.py" and path not in original_sources:
                        path.unlink()
            for path, source in original_sources.items():
                write_atomic(path, source)
            forget_migration_modules()
            raise
        return labels, len(migration_files), recorded

    def build(self) -> None:
        """Repair generated sources and materialize declared app migrations."""

        tree = _generated_tree(self.runtime_dir, self.render_sources())
        if tree.drift():
            self._emit(tree)
        else:
            self._bind(tree.artifacts)
        self.runtime_migrations().materialize(apps=apps)

    def import_generated_models(self) -> None:
        """Import concrete modules in parent order; Django runs native class validation."""

        importlib.invalidate_caches()
        imported: set[str] = set()
        for source in self.composition.ordered_models:
            label = source._meta.app_label
            if label not in imported:
                importlib.import_module(f"{self.runtime_module}.{label}.models")
                imported.add(label)

    def emit_if_stale(self) -> bool:
        """Repair boot sources without pruning, then bind integration-owned paths."""

        sources = self.render_sources()
        changed = _generated_tree(self.runtime_dir, sources).reconcile(prune=False)
        self._bind(sources)
        return changed

    def configure_migration_modules(self) -> None:
        """Bind current generated migrations during population.

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

    def check(self) -> None:
        """Raise for generated-source or declared-migration drift."""

        drift = _generated_tree(self.runtime_dir, self.render_sources()).drift()
        if drift:
            rendered = ", ".join(str(path) for path in drift)
            raise RuntimeError(f"generated runtime is stale: {rendered}")
        self.runtime_migrations().check()
