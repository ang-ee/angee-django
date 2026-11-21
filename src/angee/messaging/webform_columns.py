"""Messaging's refusal to let a channel's form columns go while they hold a form.

The six columns are messaging's own, and they leave with a migration each
project authors. The composer's drop guard protects authoring: ``makemigrations``
refuses to autodetect the removal of a column that holds data, on the database
it runs on. Nothing protected applying. A removal authored where no channel held
a form (a developer's database) is an ordinary migration on disk, and applied to
a database whose channels do hold forms it dropped them without a word. The
``pre_migrate`` receiver here refuses that run before anything is applied, on
every project that has the columns, with or without the web forms bridge; the
database check says the same to someone who asks first.

Interim: deleted once the framework pins django-compose 0.1.0.dev14, whose drop
guard judges pending removals at migrate time. That guard is the owner of "never
drop a column that holds data", and a removal declares itself to it per
operation. With that pin the bridge's template keeps its ``RunPython`` and its
``RemoveConstraint``, writes each of its six removals as django-compose's
``IntendedRemoveField``, and can drop ``moves_webform_channels``. The template's
update ships with the pin: an unapplied project copy that still uses plain
``RemoveField`` is then refused at ``migrate``, which fails safe, and must be
copied again.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from django.apps import AppConfig
from django.apps import apps as installed_apps
from django.apps.registry import Apps
from django.core import checks
from django.core.management.base import CommandError
from django.db import connections
from django.db.migrations import Migration, RemoveField, SeparateDatabaseAndState
from django.db.migrations.exceptions import BadMigrationError, CircularDependencyError, NodeNotFoundError
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.operations.base import Operation

from angee.messaging.webforms import FORM_COLUMNS, channel_public_id, channels_holding_forms, is_move

NAMED_CHANNELS = 5
"""How many channels a refusal names by public id; the rest are counted."""

REMEDY = (
    "Delete that migration, install angee.webforms_messaging if this project has not, copy its "
    "compose_migrations/messaging/webform_channels_to_forms.py into runtime/messaging/migrations/ pinned after the "
    "messaging leaf and the webforms initial migration, run makemigrations messaging until it reports no changes, "
    "then migrate; manage.py check_webform_channels is the dry run."
)
"""What an operator does about a refusal, in one sentence."""


def removes_form_columns(migration: Migration) -> bool:
    """Whether ``migration`` removes any of the channel's form columns.

    It looks for ``RemoveField`` on messaging's channel, also among the
    database operations of a ``SeparateDatabaseAndState``. A column dropped by
    ``RunSQL``, or renamed and then removed, is out of scope: neither says in
    its operations which column leaves.
    """

    def removes(operations: Iterable[Operation]) -> bool:
        return any(
            removes(operation.database_operations) if isinstance(operation, SeparateDatabaseAndState)
            else isinstance(operation, RemoveField)
            and operation.model_name_lower == "channel"
            and operation.name in FORM_COLUMNS
            for operation in operations
        )

    return migration.app_label == "messaging" and removes(migration.operations)


def removals_ahead_of_the_move(migrations: Iterable[Migration]) -> list[Migration]:
    """Return the migrations that remove a form column before the move has run.

    ``migrations`` are in the order they would be applied. The move removes the
    columns itself once it has carried the forms, so nothing after it is ahead
    of it.
    """

    ahead: list[Migration] = []
    for migration in migrations:
        if is_move(migration):
            break
        if removes_form_columns(migration):
            ahead.append(migration)
    return ahead


def channels_at_stake(apps: Apps, using: str) -> list[dict[str, Any]]:
    """Return the webform channels on ``using`` that hold a form, by messaging's one judgment of that.

    ``apps`` is the migration state the database is in. The channel table is
    looked at first: a database that has no channel table yet, or whose table
    has lost the form columns, holds no form to lose, and nothing else is read
    there.
    """

    try:
        channel = apps.get_model("messaging", "Channel")
    except LookupError:
        return []
    connection = connections[using]
    table = channel._meta.db_table
    if table not in connection.introspection.table_names():
        return []
    with connection.cursor() as cursor:
        columns = {column.name for column in connection.introspection.get_table_description(cursor, table)}
    declared = {field.name for field in channel._meta.get_fields()}
    if not (columns.issuperset(FORM_COLUMNS) and declared.issuperset(FORM_COLUMNS)):
        return []
    return channels_holding_forms(apps, using)


def refusal(removals: list[Migration], holding: list[dict[str, Any]]) -> str:
    """Say which migrations would lose how many channels' forms, naming a few channels by public id only."""

    named = ", ".join(channel_public_id(channel["pk"]) for channel in holding[:NAMED_CHANNELS])
    more = f" and {len(holding) - NAMED_CHANNELS} more" if len(holding) > NAMED_CHANNELS else ""
    migrations = ", ".join(f"{migration.app_label}.{migration.name}" for migration in removals)
    return (
        f"{migrations} removes the form columns of {len(holding)} webform channel(s) that hold a form "
        f"({named}{more}), and it is not the move that carries them: applying it loses those forms."
    )


def refuse_removal_ahead_of_the_move(
    sender: Any, *, using: str, plan: Iterable[tuple[Migration, bool]] | None = None, apps: Apps | None = None,
    **kwargs: Any,
) -> None:
    """Abort a ``migrate`` run that would drop the forms webform channels hold.

    A ``pre_migrate`` receiver, connected for messaging's own app config, so it
    acts once per run. Django sends the signal with the plan this run will
    apply, before any operation and whether or not system checks are skipped,
    so it also covers ``provision``, which skips them. It refuses when a forward
    migration in the plan removes one of the channel's form columns
    (:func:`removes_form_columns` says what it sees) without being the move or
    following it, the channel table still has those columns, and a webform
    channel holds a form. The refusal is a ``CommandError`` that names the
    migration, counts the channels and names a few by public id, never by what
    they hold, and gives the remedy; raised here, it stops ``migrate`` with
    nothing applied. A faked run is refused too: the signal does not say that
    it is one.

    It is silent for a backwards plan, when the move comes first, when nothing
    in the plan touches the columns, on a fresh database, and where no channel
    holds a form: there the autodetected removal is the right migration.

    Interim: deleted once the framework pins django-compose 0.1.0.dev14, whose
    drop guard judges pending removals at migrate time.
    """

    del sender, kwargs
    removals = removals_ahead_of_the_move(migration for migration, backwards in plan or () if not backwards)
    if not removals or apps is None:
        return
    holding = channels_at_stake(apps, using)
    if holding:
        raise CommandError(f"{refusal(removals, holding)} Nothing was applied.\n{REMEDY}")


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
    there the receiver refuses. It is silent on a fresh database, where no
    channel holds a form, and when the project's migrations cannot be loaded:
    ``migrate`` builds the same loader next and says what is wrong in its own
    words.

    Interim: deleted once the framework pins django-compose 0.1.0.dev14, whose
    drop guard judges pending removals at migrate time.
    """

    del kwargs
    channel = installed_apps.get_model("messaging", "Channel")
    if app_configs is not None and channel._meta.app_label not in {config.label for config in app_configs}:
        return []
    errors: list[checks.CheckMessage] = []
    for alias in databases or ():
        try:
            executor = MigrationExecutor(connections[alias])
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
