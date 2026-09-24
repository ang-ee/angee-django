"""Backfill retained Decision issuers after REBAC schema sync."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Restore issuer sharing for Decisions retained before the issuer relation."""

    help = "Backfill Decision issuer tuples from run admission. Run after `manage.py rebac sync`."

    def handle(self, *args: Any, **options: Any) -> None:
        """Dispatch the idempotent workflow owner and report eligible Decisions."""

        del args, options
        written = apps.get_model("workflows", "Decision").objects.resync_issuers()
        self.stdout.write(self.style.SUCCESS(f"resync_decision_issuers: reconciled {written} Decision issuer(s)"))
