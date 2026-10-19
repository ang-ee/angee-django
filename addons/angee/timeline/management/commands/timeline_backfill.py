"""Fill the timeline ledger for existing rows through each kind's own projection."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from rebac import system_context

from angee.timeline.events import sync_events
from angee.timeline.kinds import timeline_kinds


class Command(BaseCommand):
    help = "Project every kind's existing source rows into the timeline ledger (idempotent)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--kind", action="append", default=[], help="Only these kind ids (repeatable).")

    def handle(self, *args: Any, **options: Any) -> None:
        wanted = set(options["kind"])
        for kind in timeline_kinds():
            if wanted and kind.id not in wanted:
                continue
            rows = changed = 0
            with system_context(reason="timeline.backfill"):
                for row in kind.rows():
                    rows += 1
                    changed += sync_events(row, kinds=(kind,))
            self.stdout.write(f"{kind.id}: {rows} rows, {changed} events written")
