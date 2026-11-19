"""Repair materialized Handle.party values from retained statements."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.management.base import BaseCommand
from rebac import system_context

from angee.base.models.identity import public_id_for


class Command(BaseCommand):
    """Run the explicit handle-owner repair; a second run repairs nothing."""

    help = (
        "State each handle holder no statement names, leaving confirmed and contested ones for a person, "
        "then re-resolve handles whose materialized party no longer has a decisive statement."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        """Dispatch to the Handle manager and report what it stated, left and re-resolved."""

        del args
        handle_model = apps.get_model("parties", "Handle")
        with system_context(reason="parties.resolve_stale_owners"):
            repair = handle_model.objects.resolve_stale_owners()
        self.stdout.write(
            self.style.SUCCESS(
                f"Stated {repair.stated} stored holder(s); left {len(repair.confirmed)} confirmed and "
                f"{len(repair.contested)} contested holder(s) for a person; "
                f"re-resolved {repair.resolved} stale holder(s)."
            )
        )
        if options["verbosity"] > 1:
            for state, handles in (("confirmed", repair.confirmed), ("contested", repair.contested)):
                for pk in handles:
                    self.stdout.write(f"Left {state}: {public_id_for(handle_model, pk)}")
