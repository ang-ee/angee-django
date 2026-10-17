"""Replace disposable composed-app migration history with fresh initials."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import DEFAULT_DB_ALIAS, connections

from angee.compose.runtime import Runtime


class Command(BaseCommand):
    """Dispatch a guarded composed-history reset to its runtime owner."""

    help = "Replace fully applied composed-app migration history with fresh initial migrations."

    def add_arguments(self, parser: CommandParser) -> None:
        """Select the database whose composed migration records are replaced."""

        parser.add_argument("--database", default=DEFAULT_DB_ALIAS, choices=tuple(connections))

    def handle(self, *args: Any, **options: Any) -> None:
        """Reset composed migration history and report the affected scope."""

        del args
        try:
            labels, removed, recorded = Runtime.from_django().reset_migration_history(
                database=options["database"],
            )
        except RuntimeError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(f"labels: {', '.join(labels)}")
        self.stdout.write(f"files removed: {removed}")
        self.stdout.write(f"migrations recorded: {recorded}")
