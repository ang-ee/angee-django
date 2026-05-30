"""Angee backend build and resource commands."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from django.core.management.base import (
    BaseCommand,
    CommandError,
    CommandParser,
)

from angee.base.emission import DriftError, clean_runtime, emit_runtime
from angee.base.models import Resource


class Command(BaseCommand):
    """Expose Angee maintenance actions through Django management."""

    help = "Run Angee build and resource commands."
    requires_system_checks: list[str] = []

    def add_arguments(self, parser: CommandParser) -> None:
        """Add subcommands."""

        subcommands = parser.add_subparsers(dest="subcommand", required=True)

        build = subcommands.add_parser("build")
        build.add_argument("--no-apply", action="store_true")
        build.add_argument("--check", action="store_true")
        build.set_defaults(handler=self._handle_build)

        clean = subcommands.add_parser("clean")
        clean.set_defaults(handler=self._handle_clean)

        resources = subcommands.add_parser("resources")
        resource_commands = resources.add_subparsers(
            dest="resource_command",
            required=True,
        )
        validate = resource_commands.add_parser("validate")
        validate.add_argument("tier", choices=Resource.Tier.values())
        validate.set_defaults(handler=self._handle_resources_validate)
        load = resource_commands.add_parser("load")
        load.add_argument("tier", choices=Resource.Tier.values())
        load.add_argument("--allow-non-dev", action="store_true")
        load.set_defaults(handler=self._handle_resources_load)
        diff = resource_commands.add_parser("diff")
        diff.add_argument("tier", choices=Resource.Tier.values())
        diff.set_defaults(handler=self._handle_resources_diff)

    def handle(self, *args: Any, **options: Any) -> None:
        """Dispatch the selected Angee subcommand."""

        del args
        handler = cast(Callable[[dict[str, Any]], None], options["handler"])
        handler(options)

    def _handle_build(self, options: dict[str, Any]) -> None:
        """Run the build."""

        try:
            result = emit_runtime(
                apply=not bool(options["no_apply"]),
                check=bool(options["check"]),
            )
        except DriftError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                "angee build: "
                f"emitted={result.emitted} "
                f"applied={result.applied} "
                f"checked={result.checked}"
            )
        )

    def _handle_clean(self, options: dict[str, Any]) -> None:
        """Clean generated runtime files."""

        del options
        clean_runtime()
        self.stdout.write(self.style.SUCCESS("angee clean: ok"))

    def _handle_resources_validate(self, options: dict[str, Any]) -> None:
        """Validate resource files for one tier."""

        tier = cast(str, options["tier"])
        result = self._resource_model().objects.validate_tier(tier=tier)
        self.stdout.write(
            self.style.SUCCESS(
                "angee resources validate: "
                f"{result.checked_files} files, {result.checked_rows} rows"
            )
        )

    def _handle_resources_load(self, options: dict[str, Any]) -> None:
        """Load resource files for one tier."""

        tier = cast(str, options["tier"])
        load_result = self._resource_model().objects.load_tier(
            tier=tier,
            allow_non_dev=bool(options["allow_non_dev"]),
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"angee resources load {tier}: "
                f"{load_result.loaded} loaded, "
                f"{load_result.skipped} unchanged"
            )
        )

    def _handle_resources_diff(self, options: dict[str, Any]) -> None:
        """Print resource file row counts for one tier."""

        tier = cast(str, options["tier"])
        self.stdout.write(self._resource_model().objects.diff_tier(tier=tier))

    def _resource_model(self) -> type[Resource]:
        """Return the concrete runtime Resource model."""

        from django.apps import apps

        return cast(type[Resource], apps.get_model("base", "Resource"))
