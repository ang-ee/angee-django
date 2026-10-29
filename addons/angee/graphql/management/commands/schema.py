"""Render and check generated GraphQL SDL files."""

from __future__ import annotations

from typing import Any

from django.core.management.base import (
    BaseCommand,
    CommandError,
    CommandParser,
)

from angee.graphql.sdl import GraphQLSdl


class Command(BaseCommand):
    """Expose GraphQL SDL rendering through Django management."""

    help = "Check prebuilt GraphQL SDL against the serving schema."
    requires_system_checks: list[str] = []

    def add_arguments(self, parser: CommandParser) -> None:
        """Add schema command arguments."""

        parser.add_argument("--check", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        """Write or check rendered GraphQL SDL files via :class:`GraphQLSdl`."""

        del args
        sdl = GraphQLSdl.from_discovery()
        try:
            if options["check"]:
                sdl.check()
                message = "schema --check: ok"
            else:
                raise CommandError("Schema artifacts are compiler-owned; run manage.py compose")
        except RuntimeError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS(message))
