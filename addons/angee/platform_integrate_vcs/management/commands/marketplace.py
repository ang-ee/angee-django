"""Sync the addon marketplace from configured VCS sources.

``marketplace sync`` refreshes every ``Source(kind="addon")`` into ``platform.Addon``
rows via the integrate source dispatch — the same ``source.refresh()`` path templates
and skills use. A framework job (no actor), so it reads under ``system_context``.
"""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.management.base import BaseCommand
from rebac import system_context


class Command(BaseCommand):
    """Refresh the marketplace from every configured addon source."""

    help = "Sync the addon marketplace from configured VCS sources (kind='addon')."

    def add_arguments(self, parser: Any) -> None:
        """Accept an optional ``sync`` subcommand for symmetry with other CLIs."""

        parser.add_argument("subcommand", nargs="?", default="sync", choices=["sync"])

    def handle(self, *args: Any, **options: Any) -> None:
        """Refresh each addon source and report the discovered count."""

        source_model = apps.get_model("integrate", "Source")
        with system_context(reason="platform_integrate_vcs.marketplace.sync"):
            sources = list(source_model.objects.filter(kind="addon"))
            discovered = sum(source.refresh() for source in sources)
        self.stdout.write(f"marketplace sync: {len(sources)} source(s), {discovered} addon(s) discovered")
