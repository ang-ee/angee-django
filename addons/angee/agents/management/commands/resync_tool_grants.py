"""Reconcile Agent.mcp_tools selections into pure REBAC tool-grant tuples."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from angee.agents.grants import resync_tool_grants


class Command(BaseCommand):
    """Backfill direct tool grants after the agents zed revision is synced."""

    help = "Reconcile Agent.mcp_tools selections into agents/tool_grant tuples."

    def handle(self, *args: Any, **options: Any) -> None:
        """Run the grant owner and report the number of mirrored selections."""

        del args, options
        written = resync_tool_grants()
        self.stdout.write(self.style.SUCCESS(f"resync_tool_grants: wrote {written} grant(s)"))
