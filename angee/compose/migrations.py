"""Materialize addon-owned Django migrations and guard autodetected drops."""

from __future__ import annotations

import hashlib
import importlib
import json
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, cast

from django.apps import AppConfig, apps
from django.apps.registry import Apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DEFAULT_DB_ALIAS, DatabaseError, connections, router, transaction
from django.db.migrations import Migration
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.graph import MigrationGraph
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.operations.fields import FieldOperation, RemoveField
from django.db.migrations.operations.models import DeleteModel, IndexOperation, ModelOperation
from django.db.migrations.recorder import MigrationRecorder
from django.db.migrations.state import ProjectState
from django.db.migrations.writer import MigrationWriter
from django.db.models.expressions import DatabaseDefault
from django.utils.module_loading import module_dir

from angee.compose.declarations import AppDeclarationSource, BuildIntegration
from angee.utils.artifacts import GeneratedTree
from angee.utils.files import write_atomic

MATERIALIZED_FOOTER = "# ANGEE MATERIALIZED MIGRATION - DO NOT EDIT"
SOURCE_SHA256_ATTR = "angee_source_sha256"


def _migration_directory(app_label: str, *, allow_missing: bool = False) -> Path:
    """Resolve the configured native package without creating files during planning."""

    module_name, _explicit = MigrationLoader.migrations_module(app_label)
    if module_name is None:
        raise ImproperlyConfigured(f"Migrations are disabled for app {app_label!r}")
    try:
        module = importlib.import_module(module_name)
        if not hasattr(module, "__path__"):
            raise ValueError("Migrations must be a Python package")
        return Path(module_dir(module))
    except (ImportError, ValueError) as error:
        if allow_missing and isinstance(error, ModuleNotFoundError) and error.name is not None and (
            module_name == error.name or module_name.startswith(f"{error.name}.")
        ):
            parent_name, _separator, basename = module_name.rpartition(".")
            config = apps.get_app_config(app_label)
            if parent_name == config.name:
                return Path(config.path) / basename
            # The native writer can initialize this immediate child later.
            # Do not reproduce its recursive custom-package search here.
            try:
                parent = importlib.import_module(parent_name)
                if not hasattr(parent, "__path__"):
                    raise ValueError("Migration parent must be a Python package")
                return Path(module_dir(parent)) / basename
            except (ImportError, ValueError) as parent_error:
                raise ImproperlyConfigured(
                    f"Initialize the importable parent package {parent_name!r} before "
                    f"materializing addon migrations into {module_name!r}."
                ) from parent_error
        raise ImproperlyConfigured(
            f"Create the importable migration package {module_name!r} configured for {app_label!r} "
            "before building or resetting its history."
        ) from error


def reset_migration_history(
    labels: Iterable[str], *, database: str = DEFAULT_DB_ALIAS,
) -> tuple[tuple[str, ...], int, int]:
    """Replace fully applied, model-clean native history, restoring files on failure.

    The caller explicitly selects the composed app labels. Django's configured
    migration packages own the files, including tracked host packages outside the
    generated runtime. This exceptional upgrade cutoff never recreates tables.
    """

    labels = tuple(sorted(set(labels)))
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
        executor.loader.project_state(), ProjectState.from_apps(apps),
    ).changes(graph=executor.loader.graph, trim_to_apps=set(labels), convert_apps=set(labels))
    if changes:
        raise RuntimeError(
            "migration history reset requires the recorded graph to match the live composed models. "
            f"Run `manage.py makemigrations {' '.join(labels)}` and "
            f"`manage.py migrate --database {database}` first."
        )

    directories_by_label = {label: _migration_directory(label) for label in labels}
    directories = tuple(directories_by_label.values())
    migration_files = tuple(sorted(
        directories_by_label[label] / f"{name}.py"
        for label, name in executor.loader.disk_migrations
        if label in directories_by_label
    ))
    original_sources = {path: path.read_bytes().decode("utf-8") for path in migration_files}
    prefixes = tuple(MigrationLoader.migrations_module(label)[0] for label in labels)

    def forget_migration_modules() -> None:
        for name in tuple(sys.modules):
            if any(name == prefix or name.startswith(f"{prefix}.") for prefix in prefixes):
                sys.modules.pop(name)
        for directory in directories:
            for bytecode in (directory / "__pycache__").glob("*.pyc"):
                bytecode.unlink()
        importlib.invalidate_caches()

    try:
        for path in migration_files:
            path.unlink()
        forget_migration_modules()
        call_command("makemigrations", *labels, interactive=False, include_header=False, verbosity=0)
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
            # Keep shared keys valid for already-applied external dependencies.
            for app_label, migration_name in fresh_nodes:
                if (app_label, migration_name) not in applied:
                    recorder.record_applied(app_label, migration_name)
            for app_label, migration_name in sorted(applied.keys() - fresh_nodes.keys()):
                if app_label in labels:
                    recorder.record_unapplied(app_label, migration_name)
            fresh_executor.loader.check_consistent_history(connection)
            recorded = recorder.migration_qs.filter(app__in=labels).count()
    except BaseException:
        for directory in directories:
            for path in directory.glob("*.py"):
                if not path.name.startswith("_") and path not in original_sources:
                    path.unlink()
        for path, source in original_sources.items():
            write_atomic(path, source)
        forget_migration_modules()
        raise
    return labels, len(migration_files), recorded


def archive_retired_migrations(
    app_label: str,
    *,
    runtime_tree: GeneratedTree,
    runtime_module: str,
    labels: Iterable[str],
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

    if not app_label.isidentifier() or app_label in labels or any(
        config.label == app_label for config in apps.get_app_configs()
    ):
        raise RuntimeError(f"{app_label!r} is not an inactive runtime app label")
    runtime_tree.reconcile(prune=False)
    root = runtime_tree.root.resolve()
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
        module = importlib.import_module(f"{runtime_module}.{label}.migrations.{path.stem}")
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


@dataclass(frozen=True, slots=True)
class MigrationSource:
    """A native migration and the immutable addon source that declares it."""

    origin: str
    migration: Migration
    applies: Callable[[ProjectState], bool]
    path: Path
    source: str
    sha256: str


@dataclass(frozen=True, slots=True)
class RuntimeMigrationPlan:
    """One addon source bound to its concrete native history."""

    origin: str
    app_label: str
    name: str
    source_path: Path
    source: str
    output_path: Path
    source_sha256: str
    dependencies: tuple[tuple[str, str], ...]
    run_before: tuple[tuple[str, str], ...]


class _PendingMigrationLoader(MigrationLoader):
    """Let Django construct and replace the complete disk-plus-pending graph."""

    def __init__(self, sources: Mapping[tuple[str, str], MigrationSource]) -> None:
        self.sources = sources
        super().__init__(None, ignore_no_migrations=True)

    def load_disk(self) -> None:
        super().load_disk()
        for node, entry in self.sources.items():
            self.disk_migrations[node] = entry.migration
            self.migrated_apps.add(node[0])
            self.unmigrated_apps.discard(node[0])
        # Native graph construction sees every internal edge before resolving
        # external symbolic references, independent of source/filesystem order.
        self.disk_migrations = dict(sorted(self.disk_migrations.items()))

    def add_internal_dependencies(self, key: tuple[str, str], migration: Migration) -> None:
        if key in self.sources:
            # Native self-app first/latest references are ignored; the bound
            # host leaf already supplies the concrete prerequisite. Normalize
            # before Django could create an internal dummy sentinel node.
            cast(list[tuple[str, str]], migration.dependencies)[:] = [
                dependency for dependency in migration.dependencies
                if dependency[0] != key[0] or self.check_key(dependency, key[0]) is not None
            ]
        super().add_internal_dependencies(key, migration)

    def add_external_dependencies(self, key: tuple[str, str], migration: Migration) -> None:
        if key in self.sources:
            origin = self.sources[key].origin
            # Freeze the native resolution before Django applies replacements.
            # Exact references to replaced originals stay exact; Django owns
            # rewiring those edges for both fresh and partial installations.
            cast(list[tuple[str, str]], migration.dependencies)[:] = RuntimeMigrations._resolve_nodes(
                self, migration.dependencies, current_app=key[0], origin=origin, kind="dependency",
            )
            cast(list[tuple[str, str]], migration.run_before)[:] = RuntimeMigrations._resolve_nodes(
                self, migration.run_before, current_app=key[0], origin=origin, kind="run_before",
            )
        super().add_external_dependencies(key, migration)


class RuntimeMigrations:
    """Bind addon-owned migrations to Django's configured migration graph.

    New filenames use the declared native migration name, so dependencies can
    name pending transitions before either is materialized. Existing names and
    copied sources remain immutable. Django owns graph order and state; guards
    validate applicability, never infer that a data transition already ran.
    """

    def __init__(
        self,
        addons: Iterable[AppConfig],
        *,
        labels: Iterable[str],
        declarations: AppDeclarationSource | None = None,
    ) -> None:
        self.addons = tuple(addons)
        self.labels = frozenset(labels)
        self.declarations = declarations if declarations is not None else AppDeclarationSource.configured()

    def plan(
        self,
        *,
        apps: Apps | None = None,
        defer_drop_check: bool = False,
        app_labels: Iterable[str] | None = None,
        allow_missing_packages: bool = False,
    ) -> tuple[RuntimeMigrationPlan, ...]:
        """Bind the complete graph before validating guards and native state.

        Planning is offline and read-only. A false guard is not evidence of a
        completed backfill: the host must retain its native chain or an explicit
        replacement baseline. Native migrate decides what has actually applied.
        """

        selected = None if app_labels is None else frozenset(app_labels)
        existing = MigrationLoader(None, ignore_no_migrations=True)
        sources = self._sources(existing, self._existing_migrations(existing))
        loader = self._bind_graph(existing, sources)
        targets = tuple(
            leaf for leaf in loader.graph.leaf_nodes()
            if selected is None or leaf[0] in selected
        )
        plans: list[RuntimeMigrationPlan] = []
        ordered = dict.fromkeys(
            node for leaf in targets for node in loader.graph.forwards_plan(leaf)
        )
        relevant = {node[0] for node in ordered}
        conflicts = {
            label: names for label, names in loader.detect_conflicts().items()
            if selected is None or label in relevant or label in selected
        }
        if conflicts:
            raise RuntimeError(f"multiple migration leaves: {conflicts}")
        outputs: set[Path] = set()
        for node in ordered:
            entry = sources.get(node)
            if entry is None:
                continue
            try:
                state = loader.project_state(nodes=[node], at_end=False)
                applicable = entry.applies(state.clone())
            except Exception as error:
                raise RuntimeError(f"{entry.origin}: applies(project_state) failed") from error
            if not isinstance(applicable, bool):
                raise RuntimeError(f"{entry.origin}: applies(project_state) must return bool")
            if not applicable:
                raise RuntimeError(
                    f"{entry.origin}: migration is not applicable to its declared prerequisite state. "
                    "A false schema guard cannot prove a data transition completed. "
                    "Keep the complete native migration chain, or supply a reviewed native "
                    "replacement baseline covering that chain."
                )
            try:
                entry.migration.mutate_state(state)
            except Exception as error:
                raise RuntimeError(f"{entry.origin}: migration state transition is invalid") from error
            output_path = _migration_directory(node[0], allow_missing=allow_missing_packages) / f"{node[1]}.py"
            if output_path.exists() or output_path.resolve() in outputs:
                raise RuntimeError(f"{entry.origin}: output migration already exists at {output_path}")
            outputs.add(output_path.resolve())
            plans.append(RuntimeMigrationPlan(
                origin=entry.origin,
                app_label=node[0],
                name=node[1],
                source_path=entry.path,
                source=entry.source,
                output_path=output_path,
                source_sha256=entry.sha256,
                dependencies=tuple(entry.migration.dependencies),
                run_before=tuple(entry.migration.run_before),
            ))
        if apps is not None and not defer_drop_check:
            trim: set[str] | None = None
            if selected is not None:
                pending = {node: sources[node] for node in ordered if node in sources}
                loader = self._bind_graph(existing, pending)
                trim = set(selected) | {node[0] for node in pending}
            self._check_autodetected_drops(
                loader,
                loader.project_state(),
                ProjectState.from_apps(apps),
                app_labels=trim,
            )
        return tuple(plans)

    def _sources(
        self,
        loader: MigrationLoader,
        existing: Mapping[str, tuple[tuple[str, str], Migration, Path]],
    ) -> dict[tuple[str, str], MigrationSource]:
        sources: dict[tuple[str, str], MigrationSource] = {}
        origins: set[str] = set()
        replaced = {node for migration in loader.replacements.values() for node in migration.replaces}
        for addon in self.addons:
            for declaration in self._declarations(addon):
                origin = f"{addon.name}:{declaration['name']}"
                if origin in origins:
                    raise RuntimeError(f"duplicate addon runtime migration origin {origin}")
                origins.add(origin)
                self._validate_declaration(declaration, origin)
                module = self._source_module(addon, declaration, origin)
                migration_class = self._migration_class(module, origin)
                if migration_class.replaces:
                    raise RuntimeError(
                        f"{origin}: declare replacements in the host's native migration package; "
                        "addon runtime migrations cannot replace other migrations"
                    )
                path = self._source_path(module, origin)
                source_bytes = path.read_bytes()
                sha256 = hashlib.sha256(source_bytes).hexdigest()
                materialized = existing.get(origin)
                if materialized is not None:
                    node, migration, _ = materialized
                    if node[0] != declaration["app_label"]:
                        raise RuntimeError(
                            f"{origin}: materialized target {node[0]!r} differs from "
                            f"declared target {declaration['app_label']!r}"
                        )
                    if getattr(migration, SOURCE_SHA256_ATTR, None) != sha256:
                        raise RuntimeError(f"{origin}: source digest changed after materialization")
                    continue
                node = (declaration["app_label"], declaration["name"])
                if node in sources or node in loader.disk_migrations:
                    raise RuntimeError(f"{origin}: duplicate native migration node {node!r}")
                if node in replaced:
                    raise RuntimeError(
                        f"{origin}: a host replacement names {node!r}, but its immutable "
                        "original migration is missing. Materialize and retain the complete "
                        "original chain before declaring a native squash; partial upgrades "
                        "must still execute that chain."
                    )
                applies = getattr(module, "applies", None)
                if not callable(applies):
                    raise RuntimeError(f"{origin}: source module must define applies(project_state)")
                sources[node] = MigrationSource(
                    origin=origin,
                    migration=migration_class(node[1], node[0]),
                    applies=applies,
                    path=path,
                    source=source_bytes.decode("utf-8"),
                    sha256=sha256,
                )
        return sources

    def _bind_graph(
        self, loader: MigrationLoader, sources: Mapping[tuple[str, str], MigrationSource],
    ) -> MigrationLoader:
        """Attach native host leaves, then let Django build and replace all edges."""

        leaves = {label: loader.graph.leaf_nodes(label) for label, _name in sources}
        for node, entry in sources.items():
            migration = entry.migration
            dependencies = tuple(
                self._dependency_node(raw, origin=entry.origin, kind="dependency")
                for raw in migration.dependencies
            )
            # Do not guess a branch for an unrelated conflicted app. Selected
            # targets and their actual native prerequisite labels are checked
            # together after the complete graph establishes that closure.
            if len(host_leaves := leaves[node[0]]) == 1 and host_leaves[0] not in dependencies:
                dependencies += (host_leaves[0],)
            cast(list[tuple[str, str]], migration.dependencies)[:] = dependencies
            cast(list[tuple[str, str]], migration.run_before)[:] = tuple(
                self._dependency_node(raw, origin=entry.origin, kind="run_before")
                for raw in migration.run_before
            )
        try:
            bound = _PendingMigrationLoader(sources)
        except Exception as error:
            origins = ", ".join(entry.origin for entry in sources.values())
            raise RuntimeError(f"{origins}: migration graph is invalid: {error}") from error
        return bound

    def check(self, *, app_labels: Iterable[str] | None = None) -> None:
        """Raise when an applicable addon migration has not been materialized."""

        if plans := self.plan(app_labels=app_labels):
            raise RuntimeError("pending addon runtime migration " + ", ".join(plan.origin for plan in plans))

    def materialize(self, *, apps: Apps, app_labels: Iterable[str] | None = None) -> tuple[Path, ...]:
        """Stage validated sources, then guard autodetected drops before migrations.

        App-label adoption may need these staging nodes before the consumer can
        add its cutover. No database operation runs here; an unsafe table drop
        still stops the following makemigrations command. A missing custom
        package requires its immediate parent to be importable; deeper missing
        parent packages must be initialized explicitly before materialization.
        """

        selected = None if app_labels is None else tuple(app_labels)
        plans = self.plan(
            apps=apps, defer_drop_check=True, app_labels=selected, allow_missing_packages=True,
        )
        rendered = tuple((plan, self._render(plan)) for plan in plans)
        # All guards, destinations and source renders passed without writes.
        # Let Django initialize custom packages only at this mutating boundary.
        for label in dict.fromkeys(plan.app_label for plan in plans):
            directory = Path(MigrationWriter(Migration("_package", label)).basedir)
            directory.mkdir(parents=True, exist_ok=True)
            initializer = directory / "__init__.py"
            if not initializer.exists():
                write_atomic(initializer, "")
        for plan, source in rendered:
            write_atomic(plan.output_path, source)
        importlib.invalidate_caches()
        if remaining := self.plan(apps=apps, app_labels=selected):
            raise RuntimeError(
                "pending addon runtime migrations after materialization: "
                + ", ".join(plan.origin for plan in remaining)
            )
        return tuple(plan.output_path for plan in plans)

    def _check_autodetected_drops(
        self, loader: MigrationLoader, from_state: ProjectState, to_state: ProjectState,
        *, app_labels: set[str] | None = None,
    ) -> None:
        """Refuse Django model deletions whose physical tables still have owners."""

        changes = MigrationAutodetector(from_state, to_state).changes(
            graph=loader.graph, trim_to_apps=app_labels,
        )
        table_owners: dict[str, list[str]] = {}
        for model in to_state.apps.get_models(include_swapped=True):
            table_owners.setdefault(model._meta.db_table, []).append(model._meta.label_lower)
        drops = []
        for app_label, migrations in sorted(changes.items()):
            for migration in migrations:
                for operation in migration.operations:
                    if not isinstance(operation, DeleteModel):
                        continue
                    model = from_state.apps.get_model(app_label, operation.name)
                    if model._meta.proxy or not model._meta.managed or model._meta.swapped:
                        continue
                    table = model._meta.db_table
                    owners = sorted(owner for owner in table_owners.get(table, ()) if owner != model._meta.label_lower)
                    if owners:
                        drops.append(
                            f"{model._meta.label_lower}: DeleteModel would drop table {table!r}, "
                            f"still owned by {', '.join(owners)}"
                        )
        if drops:
            raise RuntimeError(
                "unsafe autodetected model deletion: " + "; ".join(drops)
                + ". Declare the missing consumer cutover migration before running makemigrations."
            )

    @classmethod
    def _resolve_nodes(
        cls, loader: MigrationLoader, raw_nodes: Iterable[tuple[str, str]],
        *, current_app: str, origin: str, kind: str,
    ) -> tuple[tuple[str, str], ...]:
        nodes: list[tuple[str, str]] = []
        for raw_node in raw_nodes:
            node = cls._dependency_node(raw_node, origin=origin, kind=kind)
            try:
                checked = loader.check_key(node, current_app)
            except (IndexError, ValueError) as error:
                raise RuntimeError(f"{origin}: invalid {kind} {node!r}") from error
            if checked is None and node[1] == "__latest__" and node[0] != current_app:
                raise RuntimeError(f"{origin}: __latest__ {kind} app {node[0]!r} has no migration leaf")
            if checked is not None and checked not in nodes:
                nodes.append(checked)
        return tuple(nodes)

    def _existing_migrations(self, loader: MigrationLoader) -> dict[str, tuple[tuple[str, str], Migration, Path]]:
        existing: dict[str, tuple[tuple[str, str], Migration, Path]] = {}
        for node, migration in sorted(loader.disk_migrations.items()):
            origin = getattr(migration, "angee_origin", None)
            if origin is None:
                continue
            if not isinstance(origin, str) or not origin:
                raise RuntimeError(f"materialized runtime migration {node!r} has an invalid origin")
            if origin in existing:
                raise RuntimeError(f"duplicate materialized addon runtime migration origin {origin}")
            path = _migration_directory(node[0]) / f"{node[1]}.py"
            digest = getattr(migration, SOURCE_SHA256_ATTR, None)
            if not isinstance(digest, str) or not digest:
                raise RuntimeError(f"{origin}: materialized migration has no source digest")
            try:
                body, marker, _ = path.read_bytes().partition(MATERIALIZED_FOOTER.encode())
            except OSError as error:
                raise RuntimeError(f"{origin}: cannot read materialized migration {path}") from error
            if not marker:
                raise RuntimeError(f"{origin}: materialized migration footer is missing")
            if hashlib.sha256(body).hexdigest() != digest:
                raise RuntimeError(f"{origin}: materialized body digest changed")
            existing[origin] = (node, migration, path)
        return existing

    @staticmethod
    def _dependency_node(raw: object, *, origin: str, kind: str) -> tuple[str, str]:
        """Validate a native Django graph node without truncating extra items."""

        if isinstance(raw, str | bytes | Mapping | set | frozenset) or not isinstance(raw, Iterable):
            raise RuntimeError(f"{origin}: invalid Django {kind} {raw!r}")
        try:
            node: tuple[object, ...] = tuple(raw)
        except TypeError as error:
            raise RuntimeError(f"{origin}: invalid Django {kind} {raw!r}") from error
        if len(node) != 2 or not all(isinstance(value, str) for value in node):
            raise RuntimeError(f"{origin}: invalid Django {kind} {raw!r}")
        return cast("tuple[str, str]", node)

    def _declarations(self, addon: AppConfig) -> Iterator[Mapping[str, Any]]:
        for index, declaration in enumerate(self.declarations.migrations(addon)):
            for key in ("name", "app_label", "module"):
                if not isinstance(declaration.get(key), str) or not declaration[key]:
                    raise RuntimeError(f"{addon.name}: migrations[{index}] requires string {key}")
            yield declaration

    def _validate_declaration(self, declaration: Mapping[str, Any], origin: str) -> None:
        if declaration["app_label"] not in self.labels:
            raise RuntimeError(f"{origin}: unknown runtime migration target {declaration['app_label']!r}")
        if (
            not declaration["name"].isidentifier()
            or not declaration["name"].islower()
            or declaration["name"].startswith("_")
        ):
            raise RuntimeError(
                f"{origin}: migration name must be a lower-case Python identifier without a leading underscore"
            )

    @staticmethod
    def _source_module(addon: AppConfig, declaration: Mapping[str, Any], origin: str) -> ModuleType:
        module_name = declaration["module"]
        if not module_name.startswith(f"{addon.name}."):
            module_name = f"{addon.name}.{module_name}"
        conventional = f"{addon.name}.migrations"
        if module_name == conventional or module_name.startswith(f"{conventional}."):
            raise RuntimeError(
                f"{origin}: addon migration source must live outside Django's conventional migrations package"
            )
        try:
            return importlib.import_module(module_name)
        except Exception as error:
            raise RuntimeError(f"{origin}: could not import source migration {module_name!r}") from error

    @staticmethod
    def _migration_class(module: ModuleType, origin: str) -> type[Migration]:
        migration_class: Any = getattr(module, "Migration", None)
        if not isinstance(migration_class, type) or not issubclass(migration_class, Migration):
            raise RuntimeError(f"{origin}: source module must define a Django Migration class")
        return migration_class

    @staticmethod
    def _source_path(module: ModuleType, origin: str) -> Path:
        module_file = getattr(module, "__file__", None)
        if not module_file or Path(module_file).suffix != ".py":
            raise RuntimeError(f"{origin}: source migration must be a Python source file")
        return Path(module_file)

    @staticmethod
    def _render(plan: RuntimeMigrationPlan) -> str:
        if not plan.source.endswith("\n"):
            raise RuntimeError(f"{plan.origin}: source migration must end with a newline")
        return "".join((
            plan.source,
            f"{MATERIALIZED_FOOTER}\n",
            f"Migration.dependencies = {list(plan.dependencies)!r}\n",
            f"Migration.run_before = {list(plan.run_before)!r}\n",
            f"Migration.angee_origin = {json.dumps(plan.origin)}\n",
            f"Migration.angee_source_sha256 = {json.dumps(plan.source_sha256)}\n",
        ))

class DropGuardAutodetector(MigrationAutodetector):
    """Django's autodetector, refusing to drop a column or table that still holds data.

    ``makemigrations`` detects changes before writing, so a refusal leaves no
    migration file behind; ``migrate`` shares the autodetector, so its pending
    model-changes notice reports the same refusal.

    A column holds data when some row differs from the field's empty value: NULL
    for a nullable field, otherwise ``Field.get_default()`` (callable defaults
    evaluated; a database default has no comparable value, so every row counts).
    An auto-created many-to-many table holds data when it has any row; an explicit
    through model and a generated column store nothing of their own. Every
    database that may receive the migration, selected like Django's history
    consistency check, must prove the column empty; an unreachable or
    unconfigured database cannot, so the drop is refused. A deleted model's table
    holds data when it has a row, under the same proof. An installed capability
    may explicitly identify rows that retire with a deleted model through its
    build integration; every remaining row still blocks the drop.

    A reviewed drop is a declared runtime migration: build materializes it into
    the history first, so its removal is never autodetected here.
    """

    def changes(
        self,
        graph: MigrationGraph,
        trim_to_apps: set[str] | None = None,
        convert_apps: set[str] | None = None,
        migration_name: str | None = None,
    ) -> dict[str, list[Migration]]:
        """Return Django's changes, or refuse when a removed column or table holds data."""

        changes = super().changes(graph, trim_to_apps, convert_apps, migration_name)
        refusals = [
            refusal
            for app_label, app_migrations in sorted(changes.items())
            for migration in app_migrations
            for operation in migration.operations
            if isinstance(operation, RemoveField)
            for refusal in self._populated(app_label, operation)
        ]
        if refusals:
            raise CommandError(
                "unsafe autodetected column drop: " + "; ".join(refusals)
                + ". A column is dropped automatically only when every database that may receive the "
                "migration proves it empty (NULL for a nullable field, otherwise the field's default). "
                "Declare a cutover migration that preserves or retires this data and removes the field "
                "through the configured app declarations, then build before makemigrations."
            )
        refusals = [
            refusal
            for app_label, app_migrations in sorted(changes.items())
            for migration in app_migrations
            for operation in migration.operations
            if isinstance(operation, DeleteModel)
            for refusal in self._populated_table(app_label, operation)
        ]
        if refusals:
            raise CommandError(
                "unsafe autodetected table drop: " + "; ".join(refusals)
                + ". A deleted model's table is dropped automatically only when every database that may "
                "receive the migration proves it holds no rows except those explicitly retired by an installed "
                "build integration. Declare a cutover migration that preserves or retires its rows and deletes "
                "the model through the configured app declarations, then build before makemigrations."
            )
        return changes

    def _populated(self, app_label: str, operation: RemoveField) -> Iterator[str]:
        """Describe each target database where the removed field's storage holds data."""

        model = self.from_state.apps.get_model(app_label, operation.model_name)
        field = model._meta.get_field(operation.name)
        if field.many_to_many:
            through = field.remote_field.through
            if not through._meta.auto_created:
                return
            rows, table, column = through._base_manager.all(), through._meta.db_table, None
        elif field.concrete and not field.generated:
            empty = None if field.null else field.get_default()
            rows = model._base_manager.all()
            if empty is None:
                rows = rows.exclude(**{f"{field.attname}__isnull": True})
            elif not isinstance(empty, DatabaseDefault):
                rows = rows.exclude(**{field.attname: empty})
            table, column = model._meta.db_table, field.column
        else:
            return
        yield from self._holding(model, lambda alias: rows, f"{model._meta.label_lower}.{field.name}", table, column)

    def _populated_table(self, app_label: str, operation: DeleteModel) -> Iterator[str]:
        """Describe each target database where the deleted model's table holds protected rows."""

        model = self.from_state.apps.get_model(app_label, operation.name)
        if model._meta.proxy or not model._meta.managed or model._meta.swapped:
            return

        integrations = BuildIntegration.configured()

        def remaining(alias: str) -> Any:
            keys = {
                key for integration in integrations
                for key in integration.retired_primary_keys(model, alias)
            }
            return model._base_manager.exclude(pk__in=keys)

        yield from self._holding(model, remaining, model._meta.label_lower, model._meta.db_table, None)

    @staticmethod
    def _holding(
        model: type[Any], rows_for: Callable[[str], Any], name: str, table: str, column: str | None,
    ) -> Iterator[str]:
        """Describe each database that may receive ``model``'s migration where its ``rows_for`` exist.

        Like Django's history consistency check, a host without routers proves
        only ``default``.
        """

        storage = f"table {table}" if column is None else f"column {table}.{column}"
        aliases = connections if settings.DATABASE_ROUTERS else [DEFAULT_DB_ALIAS]
        for alias in sorted(aliases):
            if not router.allow_migrate_model(alias, model):
                continue
            connection = connections[alias]
            introspection = connection.introspection
            try:
                with connection.cursor() as cursor:
                    present = table in introspection.table_names(cursor) and (
                        column is None
                        or column in {info.name for info in introspection.get_table_description(cursor, table)}
                    )
                count = rows_for(alias).using(alias).count() if present else 0
            except (DatabaseError, ImproperlyConfigured) as error:
                yield f"{name}: cannot prove {storage} empty on database {alias!r} ({error})"
                continue
            if count:
                yield f"{name}: {storage} holds data in {count} row(s) on database {alias!r}"
