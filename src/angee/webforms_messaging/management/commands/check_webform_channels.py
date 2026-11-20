"""Judge the move of webform channels to forms before ``migrate`` runs it, changing nothing.

The move is a migration a project copies from the bridge's template
(``compose_migrations/messaging/webform_channels_to_forms.py``). It refuses
every channel it cannot move, and it does so at ``migrate``, when services are
held. This command runs the same judgment first: it finds the project's copy in
its messaging history, reads the channels in the migration state just before
that copy, and prints what the move would refuse and what it would leave out.

The channel table is looked at first, and the channels before the template is
asked for. Where the form columns are gone there is nothing to move, with or
without a copy of the template. Where they are there and no webform channel
holds a form there is nothing to move either: ``makemigrations messaging``
removes the empty columns, and no template is needed. Only a project whose
channels hold a form is asked for its copy, and with one the move is judged.
Which channels hold a form is messaging's judgment
(``angee.messaging.webforms``), the one the move itself uses.
"""

from __future__ import annotations

import sys
from typing import Any

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.migrations.loader import MigrationLoader

from angee.messaging.webforms import FORM_COLUMNS, channels_holding_forms, is_move


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
            holding = self.holding(loader, connection.alias)
            if holding == 0:
                self.stdout.write(
                    "No webform channel holds a form: nothing to move. makemigrations messaging removes the empty "
                    "columns."
                )
                return
            held = f"{holding} webform channel(s) hold a form, and this" if holding else "This"
            raise CommandError(
                f"{held} project's messaging history holds no copy of the webform channel template. Copy "
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

    def holding(self, loader: MigrationLoader, alias: str) -> int | None:
        """Count the webform channels that hold a form, in the state the applied migrations leave.

        ``None`` when that state does not declare the form columns the table
        has, so the channels cannot be read through it.
        """

        applied = [key for key in loader.applied_migrations if key in loader.graph.nodes]
        state = loader.project_state(applied).apps
        try:
            channel = state.get_model("messaging", "Channel")
        except LookupError:
            return None
        if not {field.name for field in channel._meta.get_fields()}.issuperset(FORM_COLUMNS):
            return None
        return len(channels_holding_forms(state, alias))
