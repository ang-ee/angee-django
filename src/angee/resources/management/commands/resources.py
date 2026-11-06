"""Validate, load, and inspect addon resource files."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from django.apps import AppConfig, apps
from django.core.management.base import BaseCommand, CommandParser

from angee.resources.models import Resource

DEFAULT_TIERS = ("master", "install")
"""Resource tiers selected when a command receives no tier argument."""


class Command(BaseCommand):
    """Expose resource maintenance actions through Django management."""

    help = "Validate, load, and inspect addon resources."
    requires_system_checks: list[str] = []

    def add_arguments(self, parser: CommandParser) -> None:
        """Add resource subcommands and their arguments."""

        subcommands = parser.add_subparsers(dest="subcommand", required=True)

        validate = subcommands.add_parser("validate")
        self._add_selection_arguments(validate)
        validate.set_defaults(handler=self._handle_validate)

        load = subcommands.add_parser("load")
        self._add_selection_arguments(load)
        load.add_argument("--allow-non-dev", action="store_true")
        load.add_argument("--dry-run", action="store_true")
        load.set_defaults(handler=self._handle_load)

        diff = subcommands.add_parser("diff")
        self._add_selection_arguments(diff)
        diff.set_defaults(handler=self._handle_diff)

    def handle(self, *args: Any, **options: Any) -> None:
        """Dispatch the selected resource subcommand."""

        del args
        handler = cast(Callable[[dict[str, Any]], None], options["handler"])
        handler(options)

    def _add_selection_arguments(self, parser: CommandParser) -> None:
        """Add tier selection arguments shared by all subcommands."""

        parser.add_argument(
            "tier",
            choices=Resource.Tier.values,
            default=None,
            nargs="?",
        )
        parser.add_argument("--include-demo", action="store_true")

    def _handle_validate(self, options: dict[str, Any]) -> None:
        """Validate selected resource files and print their counts."""

        tiers = self._selected_tiers(options)
        result = self._resource_model().objects.validate_addons(
            self._addons(),
            tiers=tiers,
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"resources validate ({', '.join(tiers)}): {result.checked_files} files, {result.checked_rows} rows"
            )
        )

    def _handle_load(self, options: dict[str, Any]) -> None:
        """Load selected resource files and print import counts."""

        tiers = self._selected_tiers(options)
        dry_run = bool(options.get("dry_run"))
        result = self._resource_model().objects.load_addons(
            self._addons(),
            tiers=tiers,
            allow_non_dev=bool(options["allow_non_dev"]),
            dry_run=dry_run,
        )
        suffix = " (dry run)" if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"resources load {', '.join(tiers)}{suffix}: "
                f"{result.created} created, {result.updated} updated, "
                f"{result.skipped} unchanged"
            )
        )

    def _handle_diff(self, options: dict[str, Any]) -> None:
        """Print resource row counts for selected files."""

        tiers = self._selected_tiers(options)
        counts = self._resource_model().objects.diff_addons(
            self._addons(),
            tiers=tiers,
        )
        lines = [f"tiers: {', '.join(tiers)}"]
        lines.extend(f"{display}: {count} rows" for display, count in counts)
        self.stdout.write("\n".join(lines))

    def _selected_tiers(self, options: dict[str, Any]) -> tuple[str, ...]:
        """Return explicit tier or default tiers with prerequisites."""

        tier = options.get("tier")
        tiers = [tier] if tier else list(DEFAULT_TIERS)
        if options.get("include_demo") and "demo" not in tiers:
            tiers.append("demo")
        return Resource.Tier.with_prerequisites(tiers)

    def _resource_model(self) -> type[Resource]:
        """Return the concrete resource ledger model from the app registry."""

        return cast(type[Resource], apps.get_model("resources", "Resource"))

    def _addons(self) -> tuple[AppConfig, ...]:
        """Return installed Django apps from the populated app registry."""

        return tuple(apps.get_app_configs())
