"""Move each party's creation time back to the earliest time its directory records state."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Run the parties' source-creation-time repair; a second run moves nothing."""

    help = (
        "Move each party's created_at back to the earliest creation time its directory records' stored payloads "
        "state, where that is earlier; a record whose payload states none is counted and left for a re-read."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        """Dispatch to the Party manager and report what it moved, kept and could not read."""

        del args, options
        moved, kept, unstated = apps.get_model("parties", "Party").objects.backdate_from_sources()
        self.stdout.write(
            self.style.SUCCESS(
                f"Moved {moved} part(ies) back to the earliest creation time their sources state; {kept} already "
                f"as old; {unstated} linked record(s) state no creation time yet (a re-read of the source fills it)."
            )
        )
