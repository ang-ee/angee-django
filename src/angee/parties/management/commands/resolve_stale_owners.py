"""Repair materialized Handle.party values from retained statements."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.management.base import BaseCommand, CommandParser
from rebac import system_context

from angee.base.models.identity import public_id_for


class Command(BaseCommand):
    """Run the explicit handle-owner repair; a second run repairs nothing."""

    help = (
        "State each handle holder no statement names, leaving trashed, confirmed and contested ones as they are, "
        "then re-resolve handles whose materialized party no longer has a decisive statement."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        """Accept the cap on the holders one run writes."""

        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="The most holders this run states or re-resolves (default: the Handle manager's REPAIR_LIMIT); "
            "the next run continues where it stopped.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Dispatch to the Handle manager and report what it stated, left and re-resolved, and where it stopped."""

        del args
        handle_model = apps.get_model("parties", "Handle")
        limit = handle_model.objects.REPAIR_LIMIT if options["limit"] is None else options["limit"]
        with system_context(reason="parties.resolve_stale_owners"):
            repair = handle_model.objects.resolve_stale_owners(limit=limit)
        self.stdout.write(
            self.style.SUCCESS(
                f"Stated {repair.stated} stored holder(s); left {len(repair.confirmed)} confirmed and "
                f"{len(repair.contested)} contested holder(s) for a person; "
                f"re-resolved {repair.resolved} stale holder(s)."
            )
        )
        if repair.remaining:
            self.stdout.write(
                self.style.WARNING(
                    f"Stopped at the cap of {limit} holder(s); {repair.remaining} more await the next provision "
                    "or resolve_stale_owners run."
                )
            )
        if options["verbosity"] > 1:
            for state, handles in (("confirmed", repair.confirmed), ("contested", repair.contested)):
                for pk in handles:
                    self.stdout.write(f"Left {state}: {public_id_for(handle_model, pk)}")
