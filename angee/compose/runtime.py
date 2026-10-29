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
from django.db.migrations.graph import MigrationGraph
from django.db.migrations.operations.fields import FieldOperation
from django.db.migrations.operations.models import IndexOperation, ModelOperation
from django.db.migrations.recorder import MigrationRecorder
from django.db.migrations.state import ProjectState

from angee.compose.dependencies import AddonDependencyGroup, AddonDependencyGroupResult
from angee.compose.migrations import RuntimeMigrations
from angee.compose.model_composition import ModelComposition
from angee.compose.permissions import apply_schema_paths, extension_source_map
from angee.compose.rendering import render_models
from angee.compose.web import WebRuntime
from angee.fs import GENERATED_SENTINEL, GeneratedTree, write_atomic
from angee.project import find_project_dir

_COMPOSER_WEB_SOURCES = {
    Path("web/manifest.json"),
    Path("web/tailwind.sources.css"),
}


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
    """Guard the whole runtime tree; sources never limit explicit cleanup."""

    source_roots = {path.parts[0] for path in sources}

    def owns(path: Path) -> bool:
        root = path.parts[0]
        return (
            root not in {"gql", "schemas"}
            and (root != "web" or path in _COMPOSER_WEB_SOURCES)
            # Current packages accumulate bytecode on import; retired ones must
            # still be pruned when only their bytecode remains.
            and ("__pycache__" not in path.parts or root not in source_roots | {"__pycache__"})
        )

    configured = getattr(settings, "ANGEE_RUNTIME_DIR", None)
    return GeneratedTree(
        runtime_dir,
        sources,
        owns=owns,
        sentinel=(Path("__init__.py"), GENERATED_SENTINEL),
        clean_root=Path(configured) if configured is not None else None,
    )


class Runtime:
    """Coordinate model, web, permission, dependency and migration owners.

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
        """Clean generated packages without discovery, preserving migration history."""

        runtime_module = str(getattr(settings, "ANGEE_RUNTIME_MODULE", "runtime"))
        runtime = sys.modules.get(runtime_module)
        _generated_tree(_runtime_directory(), {}).clean(current_roots=getattr(runtime, "RUNTIME_APPS", ()))

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
        web = WebRuntime(self.addons, runtime_dir=self.runtime_dir, ui=getattr(settings, "ANGEE_UI", None))
        sources.update(web.render_sources())
        sources.update(extension_source_map(self.addons, field_owners=self.composition.field_gate_owners()))
        return sources

    def _emit(self, tree: GeneratedTree) -> None:
        tree.reset()
        tree.reconcile(prune=False)
        apply_schema_paths(self.addons, self.runtime_dir, sources=tree.artifacts)

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

    def archive_retired_migrations(
        self,
        app_label: str,
        *,
        archive_dir: Path,
        dry_run: bool = False,
    ) -> tuple[str, ...]:
        """Archive an inactive, fully retired package without changing database records.

        Check every configured database sharing this runtime. Historical external
        dependency names may predate a composed-history reset: use the retired
        app's local graph only to project state onto the current graph, never to
        execute migrations. Only model/field/index operations are supported; opaque
        database operations need a separately reviewed retirement.

        The archive must be a new directory outside the runtime, on the same
        filesystem. An atomic rename preserves the complete package unchanged.
        Callers must also coordinate databases using this tree from other hosts.
        """

        if not app_label.isidentifier() or app_label in self.labels or any(
            config.label == app_label for config in apps.get_app_configs()
        ):
            raise RuntimeError(f"{app_label!r} is not an inactive runtime app label")
        _generated_tree(self.runtime_dir, {}).reconcile(prune=False)
        root = self.runtime_dir.resolve()
        package = root / app_label
        if not (package / "migrations").is_dir():
            raise RuntimeError(f"No retained migrations at {package}")
        if any(path.is_symlink() for path in (package, *package.rglob("*"))):
            raise RuntimeError("Retired runtime packages must not contain symlinks")
        if any(path.name not in {"migrations", "__init__.py", "__pycache__"} for path in package.iterdir()):
            raise RuntimeError(f"{package} still contains non-migration sources; review its retirement first")
        if archive_dir.is_symlink() or archive_dir.exists():
            raise RuntimeError(f"Archive directory already exists: {archive_dir}")
        archive_dir = archive_dir.resolve()
        if archive_dir.is_relative_to(root) or not archive_dir.parent.is_dir():
            raise RuntimeError("Archive directory must have an existing parent outside the runtime")
        if archive_dir.parent.stat().st_dev != package.stat().st_dev:
            raise RuntimeError("Archive directory must be on the runtime's filesystem for an atomic move")

        retained = {}
        for path in sorted(root.glob("*/migrations/*.py")):
            if path.name.startswith(("_", "~")):
                continue
            label = path.parent.parent.name
            module = importlib.import_module(f"{self.runtime_module}.{label}.migrations.{path.stem}")
            if Path(module.__file__).resolve() != path.resolve():
                raise RuntimeError(f"Migration import does not belong to this runtime: {path}")
            retained[label, path.stem] = module.Migration(path.stem, label)
        retired = {key: migration for key, migration in retained.items() if key[0] == app_label}
        if not retired:
            raise RuntimeError(f"No migration files found for {app_label}")
        graph = MigrationGraph()
        for key, migration in retired.items():
            if migration.replaces or migration.run_before:
                raise RuntimeError("Retirement requires history without replacements or run_before edges")
            graph.add_node(key, migration)
        for key, migration in retired.items():
            for dependency in migration.dependencies:
                if dependency[0] == app_label:
                    graph.add_dependency(migration, key, dependency)
        graph.ensure_not_cyclic()
        leaves = graph.leaf_nodes()
        if len(leaves) != 1:
            raise RuntimeError(f"Retired history requires one leaf, found {leaves}")
        ordered = graph.forwards_plan(leaves[0])
        if set(ordered) != set(retired):
            raise RuntimeError("Retired history contains disconnected migrations")

        for alias in sorted(connections):
            connection = connections[alias]
            executor = MigrationExecutor(connection)
            executor.loader.check_consistent_history(connection)
            if executor.migration_plan(executor.loader.graph.leaf_nodes()):
                raise RuntimeError(f"Apply pending migrations on {alias} before archiving retired history")
            applied = {
                key for key in MigrationRecorder(connection).applied_migrations() if key[0] == app_label
            }
            if applied != set(retired):
                raise RuntimeError(
                    f"{alias}: retained and applied {app_label} history differ; "
                    f"unapplied: {sorted(set(retired) - applied)}; missing files: {sorted(applied - set(retired))}"
                )
            for key, migration in (retained | executor.loader.disk_migrations).items():
                if key[0] != app_label and any(
                    edge[0] == app_label
                    for edge in (*migration.dependencies, *migration.run_before, *migration.replaces)
                ):
                    raise RuntimeError(f"{key} still references {app_label} history")

            state = executor.loader.project_state()
            tables: set[str] = set()
            for key in ordered:
                for operation in retired[key].operations:
                    if not isinstance(operation, (ModelOperation, FieldOperation, IndexOperation)):
                        raise RuntimeError(f"Cannot prove retirement through {key}: {operation!r}")
                    operation.state_forwards(app_label, state)
                    state.clear_delayed_apps_cache()
                    tables.update(
                        model._meta.db_table
                        for model in state.apps.get_models(include_auto_created=True)
                        if model._meta.app_label == app_label
                    )
            remaining = sorted(key for key in state.models if key[0] == app_label)
            if remaining:
                raise RuntimeError(f"{app_label} has not retired its models: {remaining}")
            present = tables.intersection(connection.introspection.table_names(include_views=True))
            if present:
                raise RuntimeError(f"{alias}: retired tables still exist: {sorted(present)}")

        if not dry_run:
            archive_dir.mkdir(mode=0o700)
            try:
                package.rename(archive_dir / app_label)
            except BaseException:
                archive_dir.rmdir()
                raise
            importlib.invalidate_caches()
        return tuple(key[1] for key in ordered)

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
        self.runtime_migrations().materialize(apps=apps)
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
        """Raise for source, dependency-group or migration drift."""

        drift = _generated_tree(self.runtime_dir, self.render_sources()).drift()
        if drift:
            rendered = ", ".join(str(path) for path in drift)
            raise RuntimeError(f"generated runtime is stale: {rendered}")
        self.addon_dependency_group.check()
        self.runtime_migrations().check()
