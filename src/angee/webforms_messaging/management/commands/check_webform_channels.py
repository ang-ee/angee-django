"""Judge the move of webform channels to forms before ``migrate`` runs it, changing nothing.

The move is a migration a project copies from the bridge's template
(``compose_migrations/messaging/webform_channels_to_forms.py``). It refuses
every channel it cannot move, and it does so at ``migrate``, when services are
held. This command runs the same judgment first: it finds the project's copy in
its messaging history, reads the channels in the migration state just before
that copy, and prints what the move would refuse and what it would leave out.

The channel table is looked at first. Where its form columns are gone there is
nothing to move, with or without a copy of the template: a project whose
channels never held a form removed the empty columns with an ordinary migration
and needs none.
"""

from __future__ import annotations

import sys
from typing import Any

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.migrations.loader import MigrationLoader

from angee.webforms_messaging.compose_migrations.messaging.webform_channels_to_forms import FORM_COLUMNS, is_move


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
        table = apps.get_model("messaging", "Channel")._meta.db_table
        columns: set[str] = set()
        if table in connection.introspection.table_names():
            with connection.cursor() as cursor:
                columns = {column.name for column in connection.introspection.get_table_description(cursor, table)}
        loader = MigrationLoader(connection)
        copies = sorted(
            key for key, migration in loader.disk_migrations.items() if key[0] == "messaging" and is_move(migration)
        )
        done = [f"{label}.{name}" for label, name in copies if (label, name) in loader.applied_migrations]
        if done:
            self.stdout.write(f"{done[-1]} is applied: the move is done, and nothing is left to check.")
            return
        if columns.isdisjoint(FORM_COLUMNS):
            self.stdout.write("The webform columns are already removed from messaging's channels: nothing to move.")
            return
        if not copies:
            raise CommandError(
                "This project's messaging history holds no copy of the webform channel template. Copy "
                "angee/webforms_messaging/compose_migrations/messaging/webform_channels_to_forms.py into "
                "runtime/messaging/migrations/ and pin its dependencies first."
            )
        key = copies[-1]
        migration = loader.disk_migrations[key]
        behind = [
            f"{label}.{name}" for label, name in migration.dependencies
            if label == "messaging" and (label, name) not in loader.applied_migrations
        ]
        if behind:
            raise CommandError(f"Apply {', '.join(behind)} first: the channels are read in the state it leaves.")
        # The project's own copy judges: its bytes are what migrate will run.
        planned = sys.modules[type(migration).__module__].planned_moves
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
