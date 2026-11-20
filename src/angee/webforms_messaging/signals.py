"""The bridge's refusal to let a channel's form columns go before the move has carried the forms.

The composer's drop guard protects authoring: ``makemigrations`` refuses to
autodetect the removal of a column that holds data, on the database it runs on.
Nothing protects applying. A removal authored where no channel held a form (a
developer's database) is an ordinary migration on disk, and applied to a
database whose channels do hold forms it drops them without a word. The
``pre_migrate`` receiver here refuses that run before anything is applied.

Interim: deleted once the framework pins django-compose 0.1.0.dev14, whose drop
guard judges pending removals at migrate time. That guard is the owner of "never
drop a column that holds data"; the bridge's database check goes with this
module.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.apps.registry import Apps
from django.core.management.base import CommandError
from django.db import connections
from django.db.migrations import Migration, RemoveField

from angee.webforms_messaging.compose_migrations.messaging.webform_channels_to_forms import (
    FORM_COLUMNS,
    channels_holding_forms,
    is_move,
    public_id,
)

NAMED_CHANNELS = 5
"""How many channels a refusal names by public id; the rest are counted."""

REMEDY = (
    "Delete that migration, copy angee/webforms_messaging/compose_migrations/messaging/webform_channels_to_forms.py "
    "into runtime/messaging/migrations/ pinned after the messaging leaf and the webforms initial migration, run "
    "makemigrations messaging until it reports no changes, then migrate; manage.py check_webform_channels is the "
    "dry run."
)
"""What an operator does about a refusal, in one sentence."""


def removes_form_columns(migration: Migration) -> bool:
    """Whether ``migration`` removes any of the channel's form columns."""

    return migration.app_label == "messaging" and any(
        isinstance(operation, RemoveField)
        and operation.model_name_lower == "channel"
        and operation.name in FORM_COLUMNS
        for operation in migration.operations
    )


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
    """Return the webform channels on ``using`` that hold a form, judged as the move judges them.

    ``apps`` is the migration state the database is in. The channel table is
    looked at first: a database that has no channel table yet, or whose table
    has lost the previous release's form columns, holds no form to lose, and
    nothing else is read there.
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

    named = ", ".join(public_id(channel["pk"]) for channel in holding[:NAMED_CHANNELS])
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

    A ``pre_migrate`` receiver: Django sends the signal with the plan this run
    will apply, before any operation and whether or not system checks are
    skipped, so it also covers ``provision``, which skips them. It refuses when
    a forward migration in the plan removes one of the channel's form columns
    without being the move (the bridge's template, which marks its own
    ``Migration``) or following it, the channel table still has those columns,
    and a webform channel holds a form. The refusal is a ``CommandError`` that
    names the migration, counts the channels and names a few by public id,
    never by what they hold, and gives the remedy; raised here, it stops
    ``migrate`` with nothing applied.

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
