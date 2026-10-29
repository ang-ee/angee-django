"""Archive already retired runtime history through the composer's guards."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from angee.compose.migrations import archive_retired_migrations
from angee.compose.runtime import _generated_tree, _runtime_directory


class Command(BaseCommand):
    """Dispatch retirement verification and archival to the runtime owner."""

    help = "Archive an inactive app's fully applied, table-free migration history."

    def add_arguments(self, parser: CommandParser) -> None:
        """Select the retired package and a new recoverable archive directory."""

        parser.add_argument("app_label")
        parser.add_argument("--archive-dir", required=True, type=Path)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        """Verify every configured database, then report the archive scope."""

        del args
        try:
            runtime_module = getattr(settings, "COMPOSE_RUNTIME_MODULE", "runtime")
            names = archive_retired_migrations(
                options["app_label"],
                runtime_tree=_generated_tree(_runtime_directory(), {}),
                runtime_module=runtime_module,
                labels=import_module(runtime_module).RUNTIME_APPS,
                archive_dir=options["archive_dir"],
                dry_run=options["dry_run"],
            )
        except (RuntimeError, OSError) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(f"{'Verified' if options['dry_run'] else 'Archived'}: {options['app_label']}")
        self.stdout.write(f"migrations: {', '.join(names)}")
        self.stdout.write(f"archive: {options['archive_dir'].resolve() / options['app_label']}")
        self.stdout.write("Database migration records are unchanged.")
