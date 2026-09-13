"""Migrate legacy agent principals and synchronize the built-in tool catalogue."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from angee.agents.grants import resync_tool_grants


class Command(BaseCommand):
    """Migrate legacy agent principals after the live-backed schema cutover."""

    help = "Migrate agent memberships to service users and synchronize built-in tools."

    def handle(self, *args: Any, **options: Any) -> None:
        """Run the migration owner and report membership tuples written."""

        del args, options
        written = resync_tool_grants()
        self.stdout.write(self.style.SUCCESS(f"resync_tool_grants: migrated {written} membership(s)"))
