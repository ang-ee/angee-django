"""Repair materialized Handle.party values from retained statements."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.management.base import BaseCommand
from rebac import system_context


class Command(BaseCommand):
    """Run the explicit stale handle-owner repair."""

    help = "Re-resolve handles whose materialized party no longer has a decisive statement."

    def handle(self, *args: Any, **options: Any) -> None:
        """Dispatch to the Handle manager and report the repaired count."""

        del args, options
        with system_context(reason="parties.resolve_stale_owners"):
            repaired = apps.get_model("parties", "Handle").objects.resolve_stale_owners()
        self.stdout.write(self.style.SUCCESS(f"Resolved {repaired} stale handle owner(s)."))
