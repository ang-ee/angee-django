"""Repair handle comparison keys computed under another version of the normalization rule."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.management.base import BaseCommand, CommandParser


class Command(BaseCommand):
    """Run the handle-key repair over the whole backlog, or a share of it; a second run saves nothing."""

    help = (
        "Save again the comparison key of each handle, of any platform, whose key was computed under another "
        "version of the handle's normalization rule, so a change of the rule reaches the rows stored before it."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        """Accept a cap on the rows one run saves."""

        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Save at most this many rows and stop; the next run continues where it stopped. "
            "Without it the run takes every row that is behind.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Dispatch to the Handle manager and report what it saved, what it could not, and where a cap stopped it."""

        del args
        limit = options["limit"]
        repair = apps.get_model("parties", "Handle").objects.renormalize_handles(limit=limit)
        self.stdout.write(self.style.SUCCESS(f"Saved the comparison key of {repair.saved} handle(s) again."))
        if repair.failed:
            self.stderr.write(
                self.style.ERROR(
                    f"The comparison key of {repair.failed} handle(s) could not be saved; the log has each error, "
                    "and the next provision or renormalize_handles run tries them again."
                )
            )
        unreached = repair.remaining - repair.failed
        if limit is not None and unreached:
            self.stdout.write(
                self.style.WARNING(
                    f"Stopped at the cap of {limit} handle(s); {unreached} more await the next provision "
                    "or renormalize_handles run."
                )
            )
