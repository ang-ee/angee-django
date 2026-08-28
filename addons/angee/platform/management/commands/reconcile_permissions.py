"""``reconcile_permissions`` — prune stale package-managed REBAC schema.

Runs check-free (``requires_system_checks = []``) because its whole job is to clear
the stale ``Schema*`` rows that would otherwise fail ``rebac.E009`` and block every
checked command. The build lifecycle runs it before ``makemigrations`` so rebuilds
after addon removal or REBAC definition changes stay green; the prune itself lives
in :mod:`angee.platform.permissions`.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from angee.platform.permissions import PermissionSchemaReconcileError, reconcile_permission_schema


class Command(BaseCommand):
    """Prune stale package-managed REBAC schema rows."""

    help = "Prune stale package-managed REBAC schema rows."
    requires_system_checks: list[str] = []

    def handle(self, *args: Any, **options: Any) -> None:
        """Run the reconcile and report how many stale rows were pruned."""

        del args, options
        try:
            pruned = reconcile_permission_schema()
        except PermissionSchemaReconcileError as error:
            raise CommandError(str(error)) from error
        message = (
            f"reconcile_permissions: pruned {pruned} stale schema row(s)"
            if pruned
            else "reconcile_permissions: ok (nothing stale)"
        )
        self.stdout.write(self.style.SUCCESS(message))
