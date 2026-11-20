"""The bridge's database check for a pending removal of a channel's form columns that is not the move.

The protection is the ``pre_migrate`` receiver (``signals``), which stops
``migrate`` itself. This check gives the same judgment to someone who asks
before migrating, with ``check --database``.

Interim: deleted once the framework pins django-compose 0.1.0.dev14, whose drop
guard judges pending removals at migrate time. The receiver goes with it.
"""

from __future__ import annotations

from collections.abc import Sequence

from django.apps import AppConfig, apps
from django.core import checks
from django.db import connections
from django.db.migrations.exceptions import BadMigrationError, CircularDependencyError, NodeNotFoundError
from django.db.migrations.executor import MigrationExecutor

from angee.webforms_messaging.signals import REMEDY, channels_at_stake, refusal, removals_ahead_of_the_move


def check_form_columns_leave_with_the_move(
    app_configs: Sequence[AppConfig] | None = None,
    databases: Sequence[str] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Name a pending migration that would drop the forms webform channels hold (``angee.E054``).

    The judgment is the receiver's: a migration ``migrate`` would apply removes
    one of the channel's form columns without being the move or following it,
    the channel table still has those columns, and a webform channel holds a
    form, read on the migration state that removal starts from. The error names
    the migration, counts the channels and names a few by public id.

    Registered under ``Tags.database``, so a plain ``check`` hands it no
    database and it reads none. It runs with ``check --database`` and at the
    start of a ``migrate`` that runs its checks; ``provision`` skips them, and
    there the receiver alone refuses. It is silent on a fresh database, where no
    channel holds a form, and when the project's migrations cannot be loaded:
    ``migrate`` builds the same loader next and says what is wrong in its own
    words. A project without the bridge has neither this check nor the
    receiver. Its webform channels store a backend key nothing registers, which
    ``angee.E052`` names under ``check --database`` and at the start of a
    ``migrate`` that runs its checks; under ``provision`` that is after the
    migrations, so nothing refuses a removal there until the bridge is
    installed.

    Interim: deleted once the framework pins django-compose 0.1.0.dev14, whose
    drop guard judges pending removals at migrate time.
    """

    del kwargs
    channel = apps.get_model("messaging", "Channel")
    if app_configs is not None and not {channel._meta.app_label, "webforms_messaging"} & {
        config.label for config in app_configs
    }:
        return []
    errors: list[checks.CheckMessage] = []
    for alias in databases or ():
        connection = connections[alias]
        try:
            executor = MigrationExecutor(connection)
        except (BadMigrationError, CircularDependencyError, NodeNotFoundError):
            continue
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        removals = removals_ahead_of_the_move(migration for migration, backwards in plan if not backwards)
        if not removals:
            continue
        before = executor.loader.project_state((removals[0].app_label, removals[0].name), at_end=False)
        holding = channels_at_stake(before.apps, alias)
        if holding:
            errors.append(checks.Error(refusal(removals, holding), hint=REMEDY, obj=channel, id="angee.E054"))
    return errors
