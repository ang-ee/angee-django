"""Dispatch an explicitly requested migration-history reset to its build owner."""

from typing import Any

from django.core.management.base import BaseCommand, CommandParser


class Command(BaseCommand):
    """Keep history authoring separate from prebuilt serving and provisioning."""

    def add_arguments(self, parser: CommandParser) -> None:
        from angee.compose.management.commands.reset_migration_history import Command as BuildCommand

        BuildCommand().add_arguments(parser)

    def handle(self, *args: Any, **options: Any) -> None:
        from angee.compose.management.commands.reset_migration_history import Command as BuildCommand

        command = BuildCommand(no_color=options["no_color"], force_color=options["force_color"])
        command.stdout = self.stdout
        command.stderr = self.stderr
        command.handle(*args, **options)
