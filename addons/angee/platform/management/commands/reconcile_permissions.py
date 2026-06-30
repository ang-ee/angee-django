"""``reconcile_permissions`` — prune orphaned REBAC schema after an addon uninstall.

Runs check-free (``requires_system_checks = []``) because its whole job is to clear
the orphaned ``Schema*`` rows that would otherwise fail ``rebac.E009`` and block every
checked command. The build lifecycle runs it before ``makemigrations`` so the rebuild
an uninstall triggers stays green; the prune itself lives in
:mod:`angee.platform.permissions`.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from angee.platform.permissions import reconcile_permission_schema


class Command(BaseCommand):
    """Prune REBAC schema for addons no longer in the composed app set."""

    help = "Prune orphaned REBAC schema rows left by an uninstalled addon."
    requires_system_checks: list[str] = []

    def handle(self, *args: Any, **options: Any) -> None:
        """Run the reconcile and report how many orphaned rows were pruned."""

        del args, options
        pruned = reconcile_permission_schema()
        message = (
            f"reconcile_permissions: pruned {pruned} orphaned schema row(s)"
            if pruned
            else "reconcile_permissions: ok (nothing orphaned)"
        )
        self.stdout.write(self.style.SUCCESS(message))
