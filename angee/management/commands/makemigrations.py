"""Dispatch explicit migration authoring to the build owner."""

from typing import Any

from django.core.management.commands.makemigrations import Command as MakeMigrationsCommand


class Command(MakeMigrationsCommand):
    """Keep compiler imports confined to an explicit authoring command."""

    def handle(self, *app_labels: str, **options: Any) -> None:
        if options.get("check_changes") or options.get("dry_run"):
            # Read-only checks validate the sealed models with Django. Source
            # declaration validation belongs to the explicit artifact build.
            super().handle(*app_labels, **options)
            return

        from angee.compose.management.commands.makemigrations import Command as BuildCommand

        command = BuildCommand(
            stdout=self.stdout, stderr=self.stderr, no_color=self.no_color, force_color=self.force_color,
        )
        command.handle(*app_labels, **options)
