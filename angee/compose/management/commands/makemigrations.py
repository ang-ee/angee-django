"""Django's ``makemigrations`` with the composer's populated-column drop guard.

Authored addon migrations enter the host graph before native autodetection.
Django still owns detection, questions, naming and writing. Installed apps
override core commands in Django's ``get_commands``, and ``angee.compose`` ships this one together with
``migrate``: Django requires both commands to share one autodetector
(``commands.E001``).
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
        if options.get("merge") or (name and not name.isidentifier()):
            super().handle(*app_labels, **options)
            return
        for label in app_labels:
            try:
                apps.get_app_config(label)
            except LookupError:
                super().handle(*app_labels, **options)
                return

        try:
            artifact = importlib.import_module(getattr(settings, "COMPOSE_RUNTIME_MODULE", "runtime"))
            labels = artifact.RUNTIME_APPS
        except (ImportError, AttributeError) as error:
            raise CommandError("Migration authoring requires prebuilt models; build the project first.") from error
        authored = RuntimeMigrations(apps.get_app_configs(), labels=labels)
        selected = app_labels or None
        try:
            if options.get("dry_run") or options.get("check_changes"):
                authored.check(app_labels=selected)
            else:
                authored.materialize(apps=apps, app_labels=selected)
        except (RuntimeError, ImproperlyConfigured) as error:
            raise CommandError(str(error)) from error
        super().handle(*app_labels, **options)

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
