"""Django's ``makemigrations`` with the composer's populated-column drop guard.

A host without composed migration history first authors native initial schema.
Authored addon transitions then enter the host graph before final autodetection.
Django still owns detection, questions, naming and writing; the host command
selects this build owner only for explicit migration authoring.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import CommandError
from django.core.management.commands.makemigrations import Command as MakeMigrationsCommand
from django.db.migrations import Migration
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.writer import MigrationWriter
from django.utils.module_loading import module_dir

from angee.compose.migrations import DropGuardAutodetector, RuntimeMigrations


class Command(MakeMigrationsCommand):
    """Refuse autodetected drops of populated columns before any file is written."""

    autodetector = DropGuardAutodetector

    def handle(self, *app_labels: str, **options: Any) -> None:
        """Bind reviewed sources before Django loads its autodetection graph."""

        # Let Django validate these inputs before a mutating authoring step.
        name = options.get("name")
        if name and not name.isidentifier():
            super().handle(*app_labels, **options)
            return
        for label in app_labels:
            try:
                apps.get_app_config(label)
            except LookupError:
                super().handle(*app_labels, **options)
                return

        merging = bool(options.get("merge"))
        if merging:
            # Existing branches have no unambiguous leaf for new declarations.
            # Django repairs that history before a subsequent staging command.
            loader = MigrationLoader(None, ignore_no_migrations=True)
            conflicts = loader.detect_conflicts()
            if any(not app_labels or label in app_labels for label in conflicts):
                super().handle(*app_labels, **options)
                return

        try:
            artifact = importlib.import_module(getattr(settings, "COMPOSE_RUNTIME_MODULE", "runtime"))
            labels = artifact.RUNTIME_APPS
        except (ImportError, AttributeError) as error:
            raise CommandError("Migration authoring requires prebuilt models; build the project first.") from error
        # Transitions require their target models in native prerequisite state.
        # Bootstrap only an entirely unauthored composed graph, never a missing
        # branch in existing history or a different native authoring intent.
        if labels and not any(options.get(flag) for flag in (
            "check_changes", "dry_run", "merge", "empty", "update",
        )):
            loader = MigrationLoader(None, ignore_no_migrations=True)
            if not any(label in labels for label, _name in loader.disk_migrations):
                super().handle(*app_labels, **options)
                importlib.invalidate_caches()
        authored = RuntimeMigrations(apps.get_app_configs(), labels=labels)
        selected = app_labels or None
        try:
            if merging and not (options.get("dry_run") or options.get("check_changes")):
                pending = authored.plan(
                    defer_drop_check=True, app_labels=selected, allow_missing_packages=True, for_merge=True,
                )
                if not pending:
                    super().handle(*app_labels, **options)
                    return
                if not name:
                    raise CommandError("Staging addon branches for --merge requires a unique --name.")
                pending_labels = {plan.app_label for plan in pending}
                if any(
                    label in pending_labels and migration.endswith(f"_{name}")
                    for label, migration in loader.disk_migrations
                ):
                    raise CommandError("Merge --name is already used in native history; choose a unique --name.")
            if options.get("dry_run") or options.get("check_changes"):
                authored.check(app_labels=selected)
            else:
                authored.materialize(apps=apps, app_labels=selected, for_merge=merging)
        except (RuntimeError, ImproperlyConfigured) as error:
            raise CommandError(str(error)) from error
        super().handle(*app_labels, **options)
        if merging:
            try:
                authored.plan(apps=apps, app_labels=selected)
            except (RuntimeError, ImproperlyConfigured) as error:
                raise CommandError(str(error)) from error

    def write_migration_files(
        self,
        changes: dict[str, list[Migration]],
        update_previous_migration_paths: dict[str, str] | None = None,
    ) -> None:
        """Preflight every native destination before any migration file is written."""

        packages: set[tuple[str, str]] = set()
        paths: set[Path] = set()
        for label, migrations in changes.items():
            for migration in migrations:
                if update_previous_migration_paths and getattr(migration, "angee_origin", None) is not None:
                    raise CommandError(
                        f"Cannot update immutable addon migration {label}.{migration.name}; "
                        "create a follow-up migration."
                    )
                writer = MigrationWriter(migration, self.include_header)
                package, _explicit = MigrationLoader.migrations_module(label)
                if package is None:
                    raise CommandError(f"Migrations are disabled for app {label!r}.")
                key = (package, writer.filename)
                if key in packages:
                    raise CommandError(f"Multiple migrations target {package}.{migration.name}.")
                packages.add(key)
                try:
                    module = importlib.import_module(package)
                except ModuleNotFoundError as error:
                    if error.name is None or not (package == error.name or package.startswith(f"{error.name}.")):
                        raise
                    if self.dry_run:
                        raise CommandError(
                            f"Migration package {package!r} is missing; a read-only run cannot create it. "
                            "Run normal makemigrations to create the native package."
                        ) from error
                    # MigrationWriter.path creates missing custom packages. Do
                    # not call it during preflight; Django owns that later write.
                    continue
                try:
                    path = (Path(module_dir(module)) / writer.filename).resolve()
                except ValueError as error:
                    raise CommandError(str(error)) from error
                # Native --update chooses a new filename and removes the old
                # one afterwards; it does not require clobbering a destination.
                if path in paths or path.exists():
                    raise CommandError(f"Refusing to overwrite existing migration {path}; choose a unique --name.")
                paths.add(path)
        super().write_migration_files(changes, update_previous_migration_paths)
