"""Native host commands for prebuilt validation, provisioning, and cleanup."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from django.core.management.base import (
    BaseCommand,
    CommandError,
    CommandParser,
)

from angee.management import provision


class Command(BaseCommand):
    """Expose Angee runtime checks, provisioning, and cleanup."""

    help = "Check, provision, and inspect prebuilt Angee runtime output."
    requires_system_checks: list[str] = []

    def add_arguments(self, parser: CommandParser) -> None:
        """Add Angee subcommands."""

        subcommands = parser.add_subparsers(dest="subcommand", required=True)

        check = subcommands.add_parser("check", help="Check prebuilt runtime drift without writing")
        check.set_defaults(handler=self._handle_check)

        clean = subcommands.add_parser("clean")
        clean.set_defaults(handler=self._handle_clean)

        provision = subcommands.add_parser(
            "provision",
            help="Bring the stack's Django runtime up from a fresh checkout.",
        )
        provision.add_argument(
            "--force-rebac",
            action="store_true",
            help="Force-overwrite REBAC schema on sync (rebac sync --force-overwrite).",
        )
        provision.add_argument(
            "--wait-db",
            type=int,
            default=60,
            metavar="SECONDS",
            help="Seconds to wait for the default database (default: 60).",
        )
        provision.add_argument(
            "--after-if-installed", action="append", default=[], metavar="COMMAND_LINE",
            help="Run an installed addon command with shell-quoted arguments (repeatable).",
        )
        provision.set_defaults(handler=self._handle_provision)

    def handle(self, *args: Any, **options: Any) -> None:
        """Dispatch the selected subcommand."""

        del args
        handler = cast(Callable[[dict[str, Any]], None], options["handler"])
        handler(options)

    def _handle_check(self, options: dict[str, Any]) -> None:
        """Check explicit build inputs once without registering a serving-time scan."""

        from angee.compose.runtime import Runtime

        del options
        try:
            Runtime.from_django().check()
        except RuntimeError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS("angee check: ok"))

    def _handle_clean(self, options: dict[str, Any]) -> None:
        """Delete generated runtime sources."""

        from angee.compose.runtime import Runtime

        del options
        try:
            Runtime.clean_configured()
        except RuntimeError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS("angee clean: ok"))

    def _handle_provision(self, options: dict[str, Any]) -> None:
        """Provision the already-built project through native Django commands."""

        provision.run(self, options)
