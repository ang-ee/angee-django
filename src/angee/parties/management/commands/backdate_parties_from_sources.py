"""Move each party's creation time back to the earliest time its directory records state."""

from __future__ import annotations

from datetime import UTC
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
        """Dispatch to the Party manager and report what it moved, kept and could not read: counts and times only."""

        del args, options
        moved, kept, unstated, oldest, newest = apps.get_model("parties", "Party").objects.backdate_from_sources()
        span = (
            f"; their new times run from {oldest.astimezone(UTC).isoformat()} to {newest.astimezone(UTC).isoformat()}"
            if oldest is not None and newest is not None
            else ""
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Moved {moved} part(ies) back to the earliest creation time their sources state{span}; {kept} "
                f"already as old; {unstated} linked record(s) state no creation time yet (a re-read of the source "
                "fills it)."
            )
        )
