"""Repair handle keys computed under another version of the normalization rule."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.management.base import BaseCommand, CommandParser


class Command(BaseCommand):
    """Run the handle-key repair over the whole backlog, or a share of it; a second run saves nothing."""

    help = (
        "Save again the keys of each handle, of any platform, whose keys were computed under another version of "
        "the handle's normalization rule, so a change of the rule reaches the rows stored before it; a live email "
        "handle that spells the address of another is folded into the older one and kept as a redirect."
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
        """Dispatch to the Handle manager and report what it saved, folded, could not fold or save, and where a cap
        stopped it."""

        del args
        limit = options["limit"]
        repair = apps.get_model("parties", "Handle").objects.renormalize_handles(limit=limit)
        self.stdout.write(self.style.SUCCESS(f"Saved the keys of {repair.saved} handle(s) again."))
        if repair.folded:
            self.stdout.write(
                f"Folded {repair.folded} handle(s) into the older handle of the same address; "
                "each is kept as a redirect."
            )
        if repair.refused:
            self.stderr.write(
                self.style.WARNING(
                    f"{repair.refused} handle(s) spell an address another handle owns and could not be folded; "
                    "the log names each and why."
                )
            )
        if repair.failed:
            self.stderr.write(
                self.style.ERROR(
                    f"The keys of {repair.failed} handle(s) could not be saved; the log has each error, "
                    "and the next provision or renormalize_handles run tries them again."
                )
            )
        unreached = repair.remaining - repair.failed - repair.refused
        if limit is not None and unreached:
            self.stdout.write(
                self.style.WARNING(
                    f"Stopped at the cap of {limit} handle(s); {unreached} more await the next provision "
                    "or renormalize_handles run."
                )
            )
