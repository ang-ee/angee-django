"""Repair handle comparison keys computed under another version of the normalization rule."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.management.base import BaseCommand, CommandParser


class Command(BaseCommand):
    """Run the explicit handle-key repair; a second run saves nothing."""

    help = (
        "Save again the comparison key of each handle, of any platform, whose key was computed under another "
        "version of the handle's normalization rule, so a change of the rule reaches the rows stored before it."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        """Accept the cap on the keys one run saves."""

        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="The most keys this run saves (default: the Handle manager's REPAIR_LIMIT); "
            "the next run continues where it stopped.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Dispatch to the Handle manager and report what it saved and where it stopped."""

        del args
        handles = apps.get_model("parties", "Handle").objects
        limit = handles.REPAIR_LIMIT if options["limit"] is None else options["limit"]
        saved, remaining = handles.renormalize_handles(limit=limit)
        self.stdout.write(self.style.SUCCESS(f"Saved the comparison key of {saved} handle(s) again."))
        if remaining:
            self.stdout.write(
                self.style.WARNING(
                    f"Stopped at the cap of {limit} handle(s); {remaining} more await the next provision "
                    "or renormalize_handles run."
                )
            )
