"""Materialize addon-owned Django migrations into composed runtime apps."""

from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from django.apps import AppConfig
from django.db.migrations import Migration
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.loader import MigrationLoader

from angee.addons import AddonMigration, addon_contract
from angee.fs import write_atomic

MATERIALIZED_FOOTER = "# ANGEE MATERIALIZED MIGRATION - DO NOT EDIT"
ORIGIN_ATTR = "angee_origin"
SOURCE_SHA256_ATTR = "angee_source_sha256"


@dataclass(frozen=True, slots=True)
class RuntimeMigrationPlan:
    """One applicable addon migration attached to a concrete runtime node."""

    origin: str
    app_label: str
    name: str
    source_path: Path
    output_path: Path
    source_sha256: str
    dependencies: tuple[tuple[str, str], ...]
    latest_dependencies: tuple[tuple[tuple[str, str], tuple[str, str]], ...]
    migration_class: type[Migration]


class RuntimeMigrations:
    """Plan and materialize addon-owned migrations into runtime app graphs."""

    def __init__(
        self,
        addons: Iterable[AppConfig],
        *,
        runtime_dir: Path,
        runtime_module: str,
        labels: Iterable[str],
    ) -> None:
        self.addons = tuple(addons)
        self.runtime_dir = runtime_dir
        self.runtime_module = runtime_module
        self.labels = frozenset(labels)

    def plan(self) -> tuple[RuntimeMigrationPlan, ...]:
        """Return every applicable write after validating the complete graph."""

        loader = MigrationLoader(None, ignore_no_migrations=True)
        existing = self._existing_migrations(loader)
        state = loader.project_state()
        plans: list[RuntimeMigrationPlan] = []
        next_numbers: dict[str, int] = {}
        leaves: dict[str, tuple[str, str] | None] = {}
        declared_origins: set[str] = set()

        for addon in self.addons:
            contract = addon_contract(addon)
            if contract is None:
                continue
            for declaration in contract.migrations:
                origin = f"{addon.name}:{declaration.name}"
                if origin in declared_origins:
                    raise RuntimeError(f"duplicate addon runtime migration origin {origin}")
                declared_origins.add(origin)
                self._validate_declaration(declaration, origin)
                module = self._source_module(addon, declaration, origin)
                migration_class = self._migration_class(module, origin)
                if migration_class.replaces:
                    raise RuntimeError(f"{origin}: addon runtime migrations cannot replace other migrations")
                source_path = self._source_path(module, origin)
                source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
                materialized = existing.get(origin)
                if materialized is not None:
                    node, migration, _ = materialized
                    if node[0] != declaration.app_label:
                        raise RuntimeError(
                            f"{origin}: materialized target {node[0]!r} differs from "
                            f"declared target {declaration.app_label!r}"
                        )
                    if getattr(migration, SOURCE_SHA256_ATTR, None) != source_sha256:
                        raise RuntimeError(f"{origin}: source digest changed after materialization")
                    continue
                applies = getattr(module, "applies", None)
                if not callable(applies):
                    raise RuntimeError(f"{origin}: source module must define applies(project_state)")
                try:
                    applicable = applies(state.clone())
                except Exception as error:
                    raise RuntimeError(f"{origin}: applies(project_state) failed") from error
                if not isinstance(applicable, bool):
                    raise RuntimeError(f"{origin}: applies(project_state) must return bool")
                if not applicable:
                    continue

                if declaration.app_label not in next_numbers:
                    next_numbers[declaration.app_label] = self._next_number(loader, declaration.app_label)
                    leaves[declaration.app_label] = self._target_leaf(
                        loader,
                        declaration.app_label,
                        origin=origin,
                    )
                number = next_numbers[declaration.app_label]
                next_numbers[declaration.app_label] += 1
                name = f"{number:04d}_{declaration.name}"
                node = (declaration.app_label, name)
                target_leaf = leaves[declaration.app_label]
                dependencies, latest_dependencies = self._resolve_dependencies(
                    loader,
                    migration_class.dependencies,
                    current_app=declaration.app_label,
                    origin=origin,
                )
                if target_leaf is not None and target_leaf not in dependencies:
                    dependencies += (target_leaf,)
                output_path = (
                    self.runtime_dir
                    / declaration.app_label
                    / "migrations"
                    / f"{name}.py"
                )
                if output_path.exists():
                    raise RuntimeError(f"{origin}: output migration already exists at {output_path}")
                plan = RuntimeMigrationPlan(
                    origin=origin,
                    app_label=declaration.app_label,
                    name=name,
                    source_path=source_path,
                    output_path=output_path,
                    source_sha256=source_sha256,
                    dependencies=dependencies,
                    latest_dependencies=latest_dependencies,
                    migration_class=migration_class,
                )
                migration = migration_class(name, declaration.app_label)
                migration.dependencies = list(dependencies)
                try:
                    loader.graph.add_node(node, migration)
                    for dependency in dependencies:
                        loader.graph.add_dependency(migration, node, dependency)
                    for run_before in self._resolve_run_before(
                        loader,
                        migration.run_before,
                        current_app=declaration.app_label,
                        origin=origin,
                    ):
                        loader.graph.add_dependency(migration, run_before, node)
                    loader.graph.validate_consistency()
                    loader.graph.ensure_not_cyclic()
                except Exception as error:
                    raise RuntimeError(f"{origin}: migration graph is invalid") from error
                try:
                    state = migration.mutate_state(state)
                except Exception as error:
                    raise RuntimeError(f"{origin}: migration state transition is invalid") from error
                leaves[declaration.app_label] = node
                plans.append(plan)

        return tuple(plans)

    def check(self) -> None:
        """Raise when an applicable addon migration has not been materialized."""

        plans = self.plan()
        if plans:
            origins = ", ".join(plan.origin for plan in plans)
            raise RuntimeError(f"pending addon runtime migration {origins}")

    def materialize(self) -> tuple[Path, ...]:
        """Copy every applicable source migration after the plan validates."""

        plans = self.plan()
        rendered = tuple((plan, self._render(plan)) for plan in plans)
        for plan, source in rendered:
            write_atomic(plan.output_path, source)
        importlib.invalidate_caches()
        if plans:
            MigrationLoader(None, ignore_no_migrations=True)
        return tuple(plan.output_path for plan in plans)

    @staticmethod
    def _next_number(loader: MigrationLoader, app_label: str) -> int:
        numbers = [
            MigrationAutodetector.parse_number(name)
            for label, name in loader.disk_migrations
            if label == app_label
        ]
        return max((number for number in numbers if number is not None), default=0) + 1

    @staticmethod
    def _target_leaf(
        loader: MigrationLoader,
        app_label: str,
        *,
        origin: str,
        required: bool = False,
    ) -> tuple[str, str] | None:
        leaves = loader.graph.leaf_nodes(app_label)
        if len(leaves) > 1:
            rendered = ", ".join(name for _, name in leaves)
            raise RuntimeError(f"{origin}: runtime migration target {app_label!r} has multiple leaves: {rendered}")
        if required and not leaves:
            raise RuntimeError(f"{origin}: __latest__ dependency app {app_label!r} has no migration leaf")
        return leaves[0] if leaves else None

    def _resolve_dependencies(
        self,
        loader: MigrationLoader,
        raw_dependencies: Iterable[tuple[str, str]],
        *,
        current_app: str,
        origin: str,
    ) -> tuple[
        tuple[tuple[str, str], ...],
        tuple[tuple[tuple[str, str], tuple[str, str]], ...],
    ]:
        dependencies: list[tuple[str, str]] = []
        latest: list[tuple[tuple[str, str], tuple[str, str]]] = []
        for raw_dependency in raw_dependencies:
            try:
                dependency = tuple(raw_dependency)
            except TypeError as error:
                raise RuntimeError(
                    f"{origin}: invalid Django migration dependency {raw_dependency!r}"
                ) from error
            if len(dependency) != 2 or not all(isinstance(value, str) for value in dependency):
                raise RuntimeError(f"{origin}: invalid Django migration dependency {raw_dependency!r}")
            node = (dependency[0], dependency[1])
            if node[1] == "__latest__":
                resolved = self._target_leaf(loader, node[0], origin=origin, required=True)
                assert resolved is not None
                dependencies.append(resolved)
                latest.append((node, resolved))
                continue
            try:
                checked = loader.check_key(node, current_app)
            except (IndexError, ValueError) as error:
                raise RuntimeError(f"{origin}: invalid dependency {node!r}") from error
            if checked is not None:
                dependencies.append(checked)
        return tuple(dependencies), tuple(latest)

    @staticmethod
    def _resolve_run_before(
        loader: MigrationLoader,
        raw_nodes: Iterable[tuple[str, str]],
        *,
        current_app: str,
        origin: str,
    ) -> tuple[tuple[str, str], ...]:
        nodes: list[tuple[str, str]] = []
        for raw_node in raw_nodes:
            try:
                node = (raw_node[0], raw_node[1])
            except (IndexError, TypeError) as error:
                raise RuntimeError(f"{origin}: invalid Django run_before node {raw_node!r}") from error
            if not all(isinstance(value, str) for value in node):
                raise RuntimeError(f"{origin}: invalid Django run_before node {raw_node!r}")
            if node[1] == "__latest__":
                raise RuntimeError(f"{origin}: run_before does not support __latest__")
            try:
                checked = loader.check_key(node, current_app)
            except (IndexError, ValueError) as error:
                raise RuntimeError(f"{origin}: invalid run_before node {node!r}") from error
            if checked is not None:
                nodes.append(checked)
        return tuple(nodes)

    def _existing_migrations(
        self,
        loader: MigrationLoader,
    ) -> dict[str, tuple[tuple[str, str], Migration, Path]]:
        existing: dict[str, tuple[tuple[str, str], Migration, Path]] = {}
        for node, migration in loader.disk_migrations.items():
            origin = getattr(migration, ORIGIN_ATTR, None)
            if origin is None:
                continue
            if not isinstance(origin, str) or not origin:
                raise RuntimeError(f"materialized runtime migration {node!r} has an invalid origin")
            if origin in existing:
                raise RuntimeError(f"duplicate materialized addon runtime migration origin {origin}")
            path = self.runtime_dir / node[0] / "migrations" / f"{node[1]}.py"
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

    def _validate_declaration(self, declaration: AddonMigration, origin: str) -> None:
        if declaration.app_label not in self.labels:
            raise RuntimeError(f"{origin}: unknown runtime migration target {declaration.app_label!r}")
        if not declaration.name.isidentifier() or not declaration.name.islower():
            raise RuntimeError(f"{origin}: migration name must be a lower-case Python identifier")

    @staticmethod
    def _source_module(addon: AppConfig, declaration: AddonMigration, origin: str) -> ModuleType:
        module_name = declaration.module
        if not module_name.startswith(f"{addon.name}."):
            module_name = f"{addon.name}.{module_name}"
        conventional = f"{addon.name}.migrations"
        if module_name == conventional or module_name.startswith(f"{conventional}."):
            raise RuntimeError(
                f"{origin}: addon migration source must live outside Django's "
                "conventional migrations package"
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
        source = plan.source_path.read_text(encoding="utf-8")
        if not source.endswith("\n"):
            raise RuntimeError(f"{plan.origin}: source migration must end with a newline")
        lines = [source, f"{MATERIALIZED_FOOTER}\n"]
        for dependency, resolved in plan.latest_dependencies:
            lines.extend(
                (
                    "Migration.dependencies = [\n",
                    f"    ({json.dumps(resolved[0])}, {json.dumps(resolved[1])}) ",
                    f"if dependency == ({json.dumps(dependency[0])}, {json.dumps(dependency[1])}) ",
                    "else dependency\n",
                    "    for dependency in Migration.dependencies\n",
                    "]\n",
                )
            )
        latest = dict(plan.latest_dependencies)
        source_dependencies = tuple(
            latest.get((dependency[0], dependency[1]), (dependency[0], dependency[1]))
            for dependency in plan.migration_class.dependencies
        )
        for dependency in plan.dependencies:
            if dependency not in source_dependencies:
                lines.append(
                    "Migration.dependencies.append("
                    f"({json.dumps(dependency[0])}, {json.dumps(dependency[1])})"
                    ")\n"
                )
        lines.extend(
            (
                f"Migration.angee_origin = {json.dumps(plan.origin)}\n",
                f"Migration.angee_source_sha256 = {json.dumps(plan.source_sha256)}\n",
            )
        )
        return "".join(lines)
