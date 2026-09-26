"""Backfill extraction target tuples after REBAC schema sync."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Mirror each retained extraction's File or Message target into REBAC."""

    help = "Backfill extraction `target` tuples so source readers read extractions. Run after `manage.py rebac sync`."

    def handle(self, *args: Any, **options: Any) -> None:
        """Dispatch the idempotent extraction owner and report mirrored targets."""

        del args, options
        written = apps.get_model("workflows_extraction", "Extraction").objects.resync_target_access()
        self.stdout.write(self.style.SUCCESS(f"resync_extraction_targets: reconciled {written} extraction target(s)"))
