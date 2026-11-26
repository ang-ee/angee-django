"""The task-recurrence template, applied as a host applies it to the previous release's rows.

The previous release's task and history tables hold ``recurrence`` and lack the due
columns: the test gives both tables that shape under a migration state derived from
the installed models, and a project's own history (``tests.project_history``) holds
a copy of the template, which the drop guard judges as ``migrate`` and ``provision``
judge it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pytest
from django.apps import apps
from django.core.management.base import CommandError
from django.db import connection, migrations
from django.db.migrations.state import ProjectState
from rebac import system_context

from angee.projects.compose_migrations.projects import task_recurrence_due_rule as template
from angee.projects.testing.models import Task
from angee.scheduling.fields import RecurrenceField
from tests.project_history import INITIAL, project_history

pytestmark = pytest.mark.django_db(transaction=True)

_COPY = "0002_task_recurrence_due_rule"
_PINS = {"HOST_MIGRATION_WITH_THE_TASK_RECURRENCE_COLUMN": INITIAL}
_TABLES = ("task", "historicaltask")
_WEEKLY = "FREQ=WEEKLY;BYDAY=FR"


def _columns(model: str) -> set[str]:
    table = apps.get_model("projects", model)._meta.db_table
    with connection.cursor() as cursor:
        return {column.name for column in connection.introspection.get_table_description(cursor, table)}


@contextmanager
def _previous_release() -> Iterator[tuple[ProjectState, Task]]:
    """A task due on a Friday, repeating weekly, as the previous release stored it; yield its state and the task.

    The task and history tables hold ``recurrence`` without the due columns, and the
    task's rows, its history's included, hold the rule. Whatever the block leaves of
    that shape is put back as the installed models declare it.
    """

    task = Task._base_manager.create(title="Send the reminder", due_date=date(2026, 9, 4))
    legacy = migrations.Migration("previous_release_task_recurrence", "projects")
    legacy.operations = [
        *(migrations.RemoveField(table, name) for table in _TABLES for name, _ in template._DUE_COLUMNS),
        *(migrations.AddField(table, "recurrence", RecurrenceField()) for table in _TABLES),
    ]
    with connection.schema_editor() as editor:
        state = legacy.apply(ProjectState.from_apps(apps), editor)
    try:
        for table in _TABLES:
            rows = state.apps.get_model("projects", table)._base_manager.order_by()
            assert rows.filter(id=task.pk).update(recurrence=_WEEKLY)
        yield state, task
    finally:
        if "recurrence" in _columns("task"):
            installed = migrations.Migration("installed_task_due_columns", "projects")
            installed.operations = [
                operation for operation in template.Migration.operations
                if not isinstance(operation, migrations.RunPython)
            ]
            with connection.schema_editor() as editor:
                installed.apply(state.clone(), editor)


def test_migrate_applies_a_copy_of_the_template_whose_tasks_hold_a_recurrence(tmp_path: Path) -> None:
    """The template moves each rule to its task's due rule and declares both drops: the drop guard lets them go."""

    with (
        system_context(reason="test task recurrence upgrade under the drop guard"),
        _previous_release() as (before, task),
        project_history(tmp_path) as history,
    ):
        history.start(before, "projects")
        history.copy("projects", template, _COPY, _PINS)

        history.migrate("projects", _COPY)

        assert history.applied("projects", _COPY)
        assert all("recurrence" not in _columns(table) for table in _TABLES)
        moved = Task._base_manager.get(pk=task.pk)
        assert moved.due_date == date(2026, 9, 4)
        assert moved.due_rule == {
            "terms": [{"anchor": "set"}], "repeat": {"kind": "calendar", "rule": _WEEKLY},
        }


def test_the_drop_guard_refuses_a_copy_that_drops_the_recurrences_undeclared(tmp_path: Path) -> None:
    """A copy without the template's declarations is refused while the rules are stored; nothing changes."""

    with (
        system_context(reason="test task recurrence upgrade under the drop guard"),
        _previous_release() as (before, task),
        project_history(tmp_path) as history,
    ):
        history.start(before, "projects")
        history.copy("projects", template, _COPY, _PINS, declarations=False)

        with pytest.raises(CommandError) as refused:
            history.migrate("projects", _COPY)

        assert not history.applied("projects", _COPY)
        assert all({"recurrence"} == _columns(table) & {"recurrence", "due_rule"} for table in _TABLES)
        stored = before.apps.get_model("projects", "task")._base_manager.filter(pk=task.pk)
        assert stored.values_list("recurrence", flat=True).get() == _WEEKLY
    said = str(refused.value)
    assert said.startswith(f"unsafe migration drop: projects.{_COPY} (Remove field recurrence from task): ")
    for table in _TABLES:
        assert f"{apps.get_model('projects', table)._meta.db_table}.recurrence holds data in " in said
    assert "Nothing was migrated" in said and "IntendedRemoveField" in said
