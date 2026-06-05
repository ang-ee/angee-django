"""Angee build-time management commands."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from django.core.management.base import (
    BaseCommand,
    CommandError,
    CommandParser,
)

from angee.compose.runtime import Runtime


class Command(BaseCommand):
    """Expose Angee runtime build and cleanup commands."""

    help = "Build and inspect Angee runtime output."
    requires_system_checks: list[str] = []

    def add_arguments(self, parser: CommandParser) -> None:
        """Add Angee subcommands."""

        subcommands = parser.add_subparsers(dest="subcommand", required=True)

        build = subcommands.add_parser("build")
        build.add_argument("--check", action="store_true")
        build.set_defaults(handler=self._handle_build)

        clean = subcommands.add_parser("clean")
        clean.set_defaults(handler=self._handle_clean)

    def handle(self, *args: Any, **options: Any) -> None:
        """Dispatch the selected subcommand."""

        del args
        handler = cast(Callable[[dict[str, Any]], None], options["handler"])
        handler(options)

    def _handle_build(self, options: dict[str, Any]) -> None:
        """Emit or check runtime source files."""

        runtime = Runtime.from_django()
        try:
            if options["check"]:
                runtime.check()
                message = "angee build --check: ok"
            else:
                if not runtime.is_current():
                    runtime.emit()
                message = "angee build: ok"
        except RuntimeError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS(message))

    def _handle_clean(self, options: dict[str, Any]) -> None:
        """Delete generated runtime sources."""

        del options
        try:
            Runtime.from_django().clean()
        except RuntimeError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS("angee clean: ok"))
