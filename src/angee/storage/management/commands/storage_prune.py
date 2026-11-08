"""Garbage-collect stale DRAFT/FAILED uploads and expired trashed files."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone
from rebac import system_context

DEFAULT_CHUNK_SIZE = 500
"""Rows materialized per purge query."""


class Command(BaseCommand):
    """Purge files whose upload or trash TTL has lapsed.

    The row-set predicates and the purge live on ``FileQuerySet``
    (``stale_drafts`` / ``expired_trash`` / ``purge``); this command parses
    arguments, dispatches and reports. Run from cron or a worker; consumers own
    the schedule.
    """

    help = "Purge stale DRAFT/FAILED uploads and expired trashed files."

    def add_arguments(self, parser: CommandParser) -> None:
        """Register the dry-run and chunk-size options."""

        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be purged without modifying state.",
        )
        parser.add_argument(
            "--chunk-size",
            type=int,
            default=DEFAULT_CHUNK_SIZE,
            help=f"Rows materialized per query (default {DEFAULT_CHUNK_SIZE}).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Run both purge passes and report the count and each failed row."""

        del args
        file_model = apps.get_model("storage", "File")
        now = timezone.now()
        draft_cutoff = now - timedelta(hours=int(settings.ANGEE_STORAGE_DRAFT_TTL_HOURS))
        trash_cutoff = now - timedelta(days=int(settings.ANGEE_STORAGE_TRASH_TTL_DAYS))

        with system_context(reason="storage.prune"):
            stale_drafts = file_model.objects.stale_drafts(draft_cutoff)
            expired_trash = file_model.objects.expired_trash(trash_cutoff)
            self.stdout.write(
                f"storage_prune: stale_drafts={stale_drafts.count()} expired_trash={expired_trash.count()}"
            )
            if options["dry_run"]:
                return
            chunk_size = max(1, int(options["chunk_size"]))
            purged = stale_drafts.purge(chunk_size=chunk_size, on_error=self._report_purge_failure)
            purged += expired_trash.purge(chunk_size=chunk_size, on_error=self._report_purge_failure)
        self.stdout.write(f"storage_prune: purged={purged}")

    def _report_purge_failure(self, pk: Any, error: Exception) -> None:
        """Report one row the file queryset could not purge."""

        self.stderr.write(f"storage_prune: purge failed for pk={pk}: {error}")
