"""Repair handle comparison keys the normalization rule no longer gives."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.management.base import BaseCommand, CommandParser


class Command(BaseCommand):
    """Run the explicit handle-key repair; a second run saves nothing."""

    help = (
        "Save again the comparison key of each handle, of any platform, that the handle's normalization rule "
        "no longer gives, so a change of the rule reaches the rows stored before it. Always reads every handle, "
        "unless --unless-applied finds the rule's version already fully applied."
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
        parser.add_argument(
            "--unless-applied",
            action="store_true",
            help="Read no handle when the normalization rule's version is recorded as fully applied to the stored "
            "keys: what provisioning passes, so it reads the table only after the rule changed or a capped run.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Dispatch to the Handle manager and report what it saved and where it stopped."""

        del args
        handles = apps.get_model("parties", "Handle").objects
        if options["unless_applied"] and handles.normalization_applied():
            self.stdout.write(
                f"Handle keys follow normalization rule version {handles.model.NORMALIZATION_VERSION}; nothing read."
            )
            return
        limit = handles.REPAIR_LIMIT if options["limit"] is None else options["limit"]
        rekeyed, remaining = handles.renormalize_handles(limit=limit)
        self.stdout.write(self.style.SUCCESS(f"Saved the comparison key of {rekeyed} handle(s) again."))
        if remaining:
            self.stdout.write(
                self.style.WARNING(
                    f"Stopped at the cap of {limit} handle(s); {remaining} more await the next provision "
                    "or renormalize_handles run."
                )
            )
