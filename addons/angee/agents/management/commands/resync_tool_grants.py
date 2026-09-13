"""Reconcile Agent.mcp_tools selections into pure REBAC tool-grant tuples."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from angee.agents.grants import resync_tool_grants


class Command(BaseCommand):
    """Migrate agent principals and backfill grants after schema revision 5."""

    help = "Migrate agent memberships to service users and reconcile Agent.mcp_tools grants."

    def handle(self, *args: Any, **options: Any) -> None:
        """Run the grant owner and report the number of mirrored selections."""

        del args, options
        written = resync_tool_grants()
        self.stdout.write(self.style.SUCCESS(f"resync_tool_grants: wrote {written} grant(s)"))
