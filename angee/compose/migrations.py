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
        state = loader.project_state()
        plans: list[RuntimeMigrationPlan] = []
        next_numbers = {
            label: self._next_number(loader, label)
            for label in self.labels
        }
        leaves = {label: self._target_leaf(loader, label) for label in self.labels}

        for addon in self.addons:
            contract = addon_contract(addon)
            if contract is None:
                continue
            for declaration in contract.migrations:
                origin = f"{contract.name}:{declaration.name}"
                self._validate_declaration(declaration, origin)
                module = self._source_module(addon, declaration, origin)
                migration_class = self._migration_class(module, origin)
                applies = getattr(module, "applies", None)
                if not callable(applies):
                    raise RuntimeError(f"{origin}: source module must define applies(project_state)")
                applicable = applies(state.clone())
                if not isinstance(applicable, bool):
                    raise RuntimeError(f"{origin}: applies(project_state) must return bool")
                if not applicable:
                    continue

                number = next_numbers[declaration.app_label]
                next_numbers[declaration.app_label] += 1
                name = f"{number:04d}_{declaration.name}"
                node = (declaration.app_label, name)
                target_leaf = leaves[declaration.app_label]
                dependencies = tuple(migration_class.dependencies)
                if target_leaf is not None and target_leaf not in dependencies:
                    dependencies += (target_leaf,)
                source_path = self._source_path(module, origin)
                plan = RuntimeMigrationPlan(
                    origin=origin,
                    app_label=declaration.app_label,
                    name=name,
                    source_path=source_path,
                    output_path=(
                        self.runtime_dir
                        / declaration.app_label
                        / "migrations"
                        / f"{name}.py"
                    ),
                    source_sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
                    dependencies=dependencies,
                    latest_dependencies=(),
                    migration_class=migration_class,
                )
                migration = migration_class(name, declaration.app_label)
                migration.dependencies = list(dependencies)
                loader.graph.add_node(node, migration)
                for dependency in dependencies:
                    loader.graph.add_dependency(migration, node, dependency)
                state = migration.mutate_state(state)
                loader.graph.validate_consistency()
                loader.graph.ensure_not_cyclic()
                leaves[declaration.app_label] = node
                plans.append(plan)

        return tuple(plans)

    def materialize(self) -> tuple[Path, ...]:
        """Copy every applicable source migration after the plan validates."""

        plans = self.plan()
        for plan in plans:
            write_atomic(plan.output_path, self._render(plan))
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
    def _target_leaf(loader: MigrationLoader, app_label: str) -> tuple[str, str] | None:
        leaves = loader.graph.leaf_nodes(app_label)
        if len(leaves) > 1:
            rendered = ", ".join(name for _, name in leaves)
            raise RuntimeError(f"runtime migration target {app_label!r} has multiple leaves: {rendered}")
        return leaves[0] if leaves else None

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
        source_dependencies = tuple(plan.migration_class.dependencies)
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
