"""Judge the move of webform channels to forms before ``migrate`` runs it, changing nothing.

The move is a migration a project copies from the bridge's template
(``compose_migrations/messaging/webform_channels_to_forms.py``). It refuses
every channel it cannot move, and it does so at ``migrate``, when services are
held. This command runs the same judgment first: it finds the project's copy in
its messaging history, reads the channels in the migration state just before
that copy, and prints what the move would refuse and what it would leave out.
"""

from __future__ import annotations

import sys
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.migrations import RunPython
from django.db.migrations.loader import MigrationLoader

MOVE = "move_webform_channels"
"""The name of the template's data step, by which a project's copy is found."""


class Command(BaseCommand):
    """Print what the webform channel move would refuse, and what it would leave out."""

    help = "Check the move of webform channels to forms before migrate applies it; writes nothing."
    requires_system_checks: list[str] = []

    def add_arguments(self, parser: CommandParser) -> None:
        """Accept the database whose channels are judged."""

        parser.add_argument("--database", default=DEFAULT_DB_ALIAS)

    def handle(self, *args: Any, **options: Any) -> None:
        """Find the project's copy of the template and run its judgment over the current channels."""

        connection = connections[options["database"]]
        loader = MigrationLoader(connection)
        copies = sorted(
            key for key, migration in loader.disk_migrations.items()
            if key[0] == "messaging" and any(
                isinstance(operation, RunPython) and getattr(operation.code, "__name__", "") == MOVE
                for operation in migration.operations
            )
        )
        if not copies:
            raise CommandError(
                "This project's messaging history holds no copy of the webform channel template. Copy "
                "angee/webforms_messaging/compose_migrations/messaging/webform_channels_to_forms.py into "
                "runtime/messaging/migrations/ and pin its dependencies first."
            )
        key = copies[-1]
        migration = loader.disk_migrations[key]
        if key in loader.applied_migrations:
            self.stdout.write(f"{key[0]}.{key[1]} is applied: the move is done, and nothing is left to check.")
            return
        behind = [
            f"{label}.{name}" for label, name in migration.dependencies
            if label == "messaging" and (label, name) not in loader.applied_migrations
        ]
        if behind:
            raise CommandError(f"Apply {', '.join(behind)} first: the channels are read in the state it leaves.")
        planned = getattr(sys.modules[type(migration).__module__], "planned_moves", None)
        if planned is None:
            raise CommandError(f"{key[0]}.{key[1]} is an earlier copy of the template that cannot be checked.")
        plan = planned(loader.project_state(key, at_end=False).apps, connection.alias)
        for report in plan.reports:
            self.stdout.write(report)
        for refusal in plan.refusals:
            self.stdout.write(self.style.ERROR(refusal))
        self.stdout.write(
            f"{key[0]}.{key[1]}: {len(plan.moves)} channel(s) would become forms, "
            f"{len(plan.refusals)} would be refused, {len(plan.reports)} fact(s) would be left out."
        )
        if plan.refusals:
            raise CommandError("The move would be refused. Change those channels, then check again.")
