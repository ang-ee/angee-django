"""Restore extraction-owned target relationships from immutable evidence."""

from typing import Any

from django.apps import apps
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Repair derived target access after a grant reset."""

    help = "Reconcile extraction target access after a grant reset or rebac sync."

    def handle(self, *args: Any, **options: Any) -> None:
        """Dispatch to the retention model's relationship owner."""
        written = apps.get_model("workflows_extraction.Extraction").objects.resync_target_access()
        self.stdout.write(self.style.SUCCESS(f"Restored {written} extraction target relationship(s)."))
