"""Replace disposable composed-app migration history with fresh initials."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import DEFAULT_DB_ALIAS, connections

from angee.compose.apps import BuildComposeConfig, ComposeConfig
from angee.compose.migrations import reset_migration_history


class Command(BaseCommand):
    """Dispatch a guarded composed-history reset using the prebuilt native labels."""

    help = "Replace fully applied composed-app migration history with fresh initial migrations."

    def add_arguments(self, parser: CommandParser) -> None:
        """Select the database whose composed migration records are replaced."""

        parser.add_argument("--database", default=DEFAULT_DB_ALIAS, choices=tuple(connections))

    def handle(self, *args: Any, **options: Any) -> None:
        """Reset composed migration history and report the affected scope."""

        del args
        config = apps.get_app_config("compose")
        if isinstance(config, ComposeConfig):
            runtime_labels = config.labels
        elif isinstance(config, BuildComposeConfig):
            runtime_labels = config.runtime.labels
        else:
            raise CommandError("Migration reset requires the configured composed app labels.")
        try:
            labels, removed, recorded = reset_migration_history(
                runtime_labels,
                database=options["database"],
            )
        except RuntimeError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(f"labels: {', '.join(labels)}")
        self.stdout.write(f"files removed: {removed}")
        self.stdout.write(f"migrations recorded: {recorded}")
