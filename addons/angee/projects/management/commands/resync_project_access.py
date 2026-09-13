"""Backfill projects-owned container relationships after REBAC schema sync."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from angee.projects.access import resync_project_access


class Command(BaseCommand):
    """Initialize project container tuples for existing database rows."""

    help = (
        "Reconcile Project.folder and ProjectBinding access tuples. "
        "Run after `manage.py rebac sync` when installing the project cascade schema."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        """Run the projects access owner and report distinct tuples written."""

        del args, options
        written = resync_project_access()
        self.stdout.write(self.style.SUCCESS(f"resync_project_access: wrote {written} relationship(s)"))
