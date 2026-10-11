"""Tasks about a record: the record's gates, its chatter, the agenda, GraphQL and the cutover."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import connection, models
from django.db.migrations.state import ModelState, ProjectState
from rebac import (
    PermissionDenied,
    RelationshipTuple,
    actor_context,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)

from angee.messaging.runtime_migrations import activities_need_projects
from angee.messaging.testing.models import Message, Part, ThreadAttachment
from angee.projects.runtime_migrations import card_tasks
from angee.projects.testing.models import Task
from tests.chatterdemo.models import ChatterDoc
from tests.conftest import create_platform_admin
from tests.test_messaging import ThreadedTicket

User = get_user_model()
pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("activity_catalog")]


def _user(name: str) -> Any:
    with system_context(reason="test.card_tasks.user"):
        return User.objects.create_user(username=name, email=f"{name}@example.com")


def _grant(resource: Any, relation: str, user: Any) -> None:
    with system_context(reason="test.card_tasks.grant"):
        write_relationships([RelationshipTuple(to_object_ref(resource), relation, to_subject_ref(user))])


def _doc(title: str = "Gated card") -> Any:
    with system_context(reason="test.card_tasks.doc"):
        return ChatterDoc.objects.create(title=title)


def _as(user: Any, record: Any) -> Any:
    """Re-read ``record`` under ``user``, as a request would."""

    with actor_context(user):
        return type(record)._default_manager.get(pk=record.pk)


def _text(message: Any) -> str:
    return Part._base_manager.select_related("fragment").get(message=message).fragment.text


def test_card_tasks_ride_the_records_read_and_write_gates() -> None:
    writer, reader, stranger = _user("card-writer"), _user("card-reader"), _user("card-stranger")
    doc = _doc()
    _grant(doc, "writer", writer)
    _grant(doc, "reader", reader)

    with actor_context(writer):
        task = Task.objects.schedule(_as(writer, doc), title="Chase the supplier", assignee=writer)
        assert list(Task.objects.for_record(_as(writer, doc))) == [task]
    with actor_context(reader):
        assert list(Task.objects.for_record(_as(reader, doc))) == [task]
        with pytest.raises(PermissionDenied, match="requires 'write' access"):
            Task.objects.schedule(_as(reader, doc), title="Not allowed")
    with actor_context(stranger), pytest.raises(ChatterDoc.DoesNotExist):
        _as(stranger, doc)
    assert task.subject_id == ThreadAttachment._base_manager.get(object_id=doc.pk, role="chatter").pk


def test_a_record_writer_completes_and_cancels_tasks_they_neither_own_nor_hold() -> None:
    admin = create_platform_admin("card-admin")
    writer, reader = _user("card-closer"), _user("card-watcher")
    doc = _doc()
    _grant(doc, "writer", writer)
    _grant(doc, "reader", reader)
    with actor_context(admin):
        first = Task.objects.schedule(_as(admin, doc), title="Confirm the bank change", activity_type="call")
        second = Task.objects.schedule(_as(admin, doc), title="Obsolete follow-up")
    assert first.assignee_id == admin.pk

    with actor_context(reader), pytest.raises(PermissionDenied):
        Task.objects.card_complete(first)
    with actor_context(writer):
        Task.objects.card_complete(first, feedback="Confirmed by phone.")
        Task.objects.card_cancel(second)

    first.refresh_from_db()
    second.refresh_from_db()
    assert (first.status, second.status, second.dropped_reason) == ("done", "dropped", "obsolete")
    (message,) = Message._base_manager.filter(subtype__key="activity_done")
    assert message.created_by_id == writer.pk
    assert _text(message) == "Call done: Confirm the bank change\n\nConfirmed by phone."


def test_a_logged_exchange_is_a_done_task_posted_on_the_record_with_no_chatter_of_its_own() -> None:
    author = _user("card-logger")
    with system_context(reason="test.card_tasks.ticket"):
        ticket = ThreadedTicket.objects.create(title="Logged case")

    with actor_context(author):
        task = Task.objects.log(
            ticket, activity_type="call", occurred_on=date(2026, 9, 1), note="Walked through the export.\nAll good.",
        )

    task.refresh_from_db()
    assert (task.status, task.title, task.due_date) == ("done", "Walked through the export.", date(2026, 9, 1))
    assert (task.assignee_id, task.activity_type_id) == (author.pk, "call")
    (message,) = Message._base_manager.filter(subtype__key="activity_done")
    assert _text(message) == "Call on 2026-09-01\n\nWalked through the export.\nAll good."
    own = ContentType.objects.get_for_model(Task)
    assert not ThreadAttachment._base_manager.filter(content_type=own, object_id=task.pk).exists()
    with actor_context(author):
        Task.objects.card_complete(task)
        task.reopen()
    assert not ThreadAttachment._base_manager.filter(content_type=own, object_id=task.pk).exists()


def test_the_agenda_lists_assigned_tasks_due_in_the_window_by_due_date() -> None:
    assignee, other = _user("agenda-holder"), _user("agenda-other")
    with system_context(reason="test.card_tasks.agenda"):
        alpha = ThreadedTicket.objects.create(title="Alpha")
        beta = ThreadedTicket.objects.create(title="Beta")
        Task.objects.schedule(beta, title="Call Beta", assignee=assignee, due_date=date(2026, 3, 10))
        Task.objects.schedule(alpha, title="Email Alpha", assignee=assignee, due_date=date(2026, 3, 5))
        Task.objects.schedule(alpha, title="Boundary", assignee=assignee, due_date=date(2026, 4, 1))
        Task.objects.schedule(alpha, title="Someone else's", assignee=other, due_date=date(2026, 3, 6))
        done = Task.objects.schedule(alpha, title="Done already", assignee=assignee, due_date=date(2026, 3, 7))
        done.complete()

    with actor_context(assignee):
        open_titles = [task.title for task in Task.objects.agenda(assignee, date(2026, 3, 1), date(2026, 4, 1))]
        all_titles = [
            task.title
            for task in Task.objects.agenda(assignee, date(2026, 3, 1), date(2026, 4, 1), include_done=True)
        ]
    assert open_titles == ["Email Alpha", "Call Beta"]
    assert all_titles == ["Email Alpha", "Done already", "Call Beta"]


# The cutover, applied to a released schema.


def _add(state: ProjectState, app_label: str, name: str, fields: list, **options: Any) -> None:
    options = {"db_table": f"test_released_{app_label}_{name.lower()}", **options}
    state.add_model(ModelState(app_label, name, fields, options=options))


def _released() -> ProjectState:
    state = ProjectState()
    key = ("id", models.BigAutoField(primary_key=True))
    account = models.ForeignKey("projects.account", models.SET_NULL, null=True, related_name="+")
    _add(state, "projects", "Account", [key])
    _add(state, "resources", "Resource", [
        ("id", models.BigAutoField(primary_key=True)),
        ("xref", models.CharField(max_length=120)),
        ("target_model", models.CharField(max_length=120)),
    ])
    _add(state, "messaging", "ThreadAttachment", [("id", models.BigAutoField(primary_key=True))])
    _add(state, "messaging", "ActivityType", [
        ("id", models.BigAutoField(primary_key=True)),
        ("key", models.SlugField(max_length=64, unique=True)),
        ("name", models.CharField(max_length=160)),
        ("glyph", models.CharField(max_length=128, blank=True, default="")),
    ])
    _add(state, "messaging", "ThreadActivity", [
        ("id", models.BigAutoField(primary_key=True)),
        ("attachment", models.ForeignKey("messaging.threadattachment", models.CASCADE, related_name="+")),
        ("user", account.clone()),
        ("activity_type", models.ForeignKey(
            "messaging.activitytype", models.PROTECT, to_field="key", db_column="activity_type", related_name="+",
        )),
        ("summary", models.CharField(max_length=256)),
        ("note", models.TextField(blank=True, default="")),
        ("feedback", models.TextField(blank=True, default="")),
        ("due_date", models.DateField(null=True)),
        ("status", models.CharField(max_length=32, default="todo")),
        ("completed_at", models.DateTimeField(null=True)),
        ("created_by", account.clone()),
        ("updated_by", account.clone()),
        ("created_at", models.DateTimeField(null=True)),
        ("updated_at", models.DateTimeField(null=True)),
    ])
    _add(state, "projects", "Task", [
        ("id", models.BigAutoField(primary_key=True)),
        ("title", models.CharField(max_length=240)),
        ("note", models.TextField(blank=True, default="")),
        ("assignee", account.clone()),
        ("owner", account.clone()),
        ("created_by", account.clone()),
        ("updated_by", account.clone()),
        ("project", models.BigIntegerField(null=True)),
        ("parent", models.BigIntegerField(null=True)),
        ("due_date", models.DateField(null=True)),
        ("status", models.CharField(max_length=32, default="open")),
        ("done_at", models.DateTimeField(null=True)),
        ("dropped_at", models.DateTimeField(null=True)),
        ("dropped_reason", models.CharField(max_length=32, null=True)),
        ("sort_order", models.FloatField()),
        ("sub_sort_order", models.FloatField()),
        ("created_at", models.DateTimeField(null=True)),
        ("updated_at", models.DateTimeField(null=True)),
        ("converted_from_activity", models.ForeignKey(
            "messaging.threadactivity", models.SET_NULL, null=True, related_name="+",
        )),
    ], constraints=[models.UniqueConstraint(fields=("converted_from_activity",),
                                            name="uq_projects_task_converted_activity")])
    return state


@contextmanager
def _released_tables(state: ProjectState) -> Iterator[None]:
    try:
        yield
    finally:
        with connection.constraint_checks_disabled(), connection.cursor() as cursor:
            existing = set(connection.introspection.table_names(cursor))
            # Children first: a dropped parent leaves SQLite unable to drop its referrers.
            for table in (
                "test_released_projects_task", "test_released_messaging_threadactivity", "projects_activitytype",
                "test_released_messaging_activitytype", "test_released_messaging_threadattachment",
                "test_released_projects_account", "test_released_resources_resource",
            ):
                if table in existing:
                    cursor.execute(f"DROP TABLE {connection.ops.quote_name(table)}")


def test_released_activities_become_card_tasks_and_messaging_retires_them() -> None:
    released = _released()
    assert card_tasks.applies(released)
    assert not activities_need_projects.applies(released)
    registry = released.apps
    with _released_tables(released):
        with connection.schema_editor() as editor:
            for model in registry.get_models():
                editor.create_model(model)
            account = registry.get_model("projects", "Account")._base_manager.create()
            attachment = registry.get_model("messaging", "ThreadAttachment")._base_manager.create()
            kinds = registry.get_model("messaging", "ActivityType")._base_manager
            kinds.create(key="todo", name="To do")
            call = kinds.create(key="call", name="Call", glyph="phone")
            activities = registry.get_model("messaging", "ThreadActivity")._base_manager
            todo = activities.create(attachment=attachment, user=account, activity_type_id="todo",
                                     summary="Chase", due_date=date(2026, 3, 1), created_by=account)
            logged = activities.create(attachment=attachment, user=account, activity_type_id="call", summary="Call",
                                       note="Walked through.", status="done", feedback="Agreed.")
            activities.create(attachment=attachment, user=account, activity_type_id="todo", summary="Stale",
                              status="canceled")
            registry.get_model("projects", "Task")._base_manager.create(
                title="Promoted", sort_order=1024.0, sub_sort_order=1024.0, converted_from_activity=todo,
            )
            ledgers = registry.get_model("resources", "Resource")._base_manager
            ledgers.create(xref="call", target_model="messaging.ActivityType")
            ledgers.create(xref="other", target_model="messaging.Message")
            upgraded = card_tasks.Migration("0099_card_tasks", "projects").apply(released, editor)

        assert ("messaging", "threadactivity") not in upgraded.models
        assert ("messaging", "activitytype") not in upgraded.models
        assert "converted_from_activity" not in upgraded.models["projects", "task"].fields
        assert not card_tasks.applies(upgraded)
        moved = upgraded.apps.get_model("projects", "ActivityType")._base_manager.get(key="call")
        assert (moved.pk, moved.glyph) == (call.pk, "phone")
        tasks = upgraded.apps.get_model("projects", "Task")._base_manager.order_by("pk")
        assert [(task.title, task.status, task.activity_type_id, task.subject_id) for task in tasks] == [
            ("Promoted", "open", None, None),
            ("Chase", "open", "todo", attachment.pk),
            ("Call", "done", "call", attachment.pk),
            ("Stale", "dropped", "todo", attachment.pk),
        ]
        converted = list(tasks)[1:]
        assert converted[0].assignee_id == converted[0].owner_id == account.pk
        assert converted[1].note == "Walked through.\n\nAgreed."
        assert converted[2].dropped_reason == "obsolete"
        assert len({task.sort_order for task in tasks}) == 4
        ledger_targets = dict(
            upgraded.apps.get_model("resources", "Resource")._base_manager.values_list("xref", "target_model")
        )
        assert ledger_targets == {"call": "projects.ActivityType", "other": "messaging.Message"}
        with connection.cursor() as cursor:
            tables = set(connection.introspection.table_names(cursor))
        assert "test_released_messaging_threadactivity" not in tables
        assert logged.pk


def test_a_stack_without_projects_refuses_to_drop_live_activities() -> None:
    released = _released()
    released.remove_model("projects", "task")
    assert activities_need_projects.applies(released)
    registry = released.apps
    with _released_tables(released):
        with pytest.raises(ValueError, match="need angee.projects"), connection.schema_editor() as editor:
            for model in registry.get_models():
                editor.create_model(model)
            account = registry.get_model("projects", "Account")._base_manager.create()
            attachment = registry.get_model("messaging", "ThreadAttachment")._base_manager.create()
            registry.get_model("messaging", "ActivityType")._base_manager.create(key="todo", name="To do")
            registry.get_model("messaging", "ThreadActivity")._base_manager.create(
                attachment=attachment, user=account, activity_type_id="todo", summary="Live",
            )
            activities_need_projects.Migration("0099_activities", "messaging").apply(released, editor)
