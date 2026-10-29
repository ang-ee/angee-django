"""Dispatch explicit retired-history archival to its build owner."""

from typing import Any

from django.core.management.base import BaseCommand, CommandParser


class Command(BaseCommand):
    """Keep migration lifecycle imports outside prebuilt serving."""

    def add_arguments(self, parser: CommandParser) -> None:
        from angee.compose.management.commands.archive_retired_migrations import Command as BuildCommand

        BuildCommand().add_arguments(parser)

    def handle(self, *args: Any, **options: Any) -> None:
        from angee.compose.management.commands.archive_retired_migrations import Command as BuildCommand

        BuildCommand(stdout=self.stdout, stderr=self.stderr).handle(*args, **options)
