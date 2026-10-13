"""Record tasks: the task schema's record arms, the record's chatter, the lifecycle and the cutover."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, migrations, models
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.graph import MigrationGraph
from django.db.migrations.state import ModelState, ProjectState
from django.db.models.signals import pre_migrate
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rebac import (
    PermissionDenied,
    RelationshipTuple,
    actor_context,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.resources import model_resource_type

from angee.base.errors import RecordAccessSubjectRefused
from angee.base.fields import SqidField
from angee.compose.migrations import DropGuardAutodetector
from angee.messaging.testing.models import Message, Part, ThreadAttachment
from angee.projects.runtime_migrations import record_tasks
from angee.projects.testing.models import Project, Queue, Task
from angee.projects.upgrade import GUARD_DISPATCH_UID, check_cutover_guard, record_task_types
from tests.chatterdemo.models import ChatterDoc, TrackedRecordParent
from tests.conftest import create_platform_admin
from tests.test_runtime_migrations import seed_ledger

User = get_user_model()
pytestmark = [
    pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("composed_permissions", "activity_catalog"),
]


def _user(name: str) -> Any:
    with system_context(reason="test.record_tasks.user"):
        return User.objects.create_user(username=name, email=f"{name}@example.com")


def _grant(resource: Any, relation: str, user: Any) -> None:
    with system_context(reason="test.record_tasks.grant"):
        write_relationships([RelationshipTuple(to_object_ref(resource), relation, to_subject_ref(user))])


def _doc(title: str = "Gated record") -> Any:
    with system_context(reason="test.record_tasks.doc"):
        return ChatterDoc.objects.create(title=title)


def _as(user: Any, row: Any) -> Any:
    """Re-read ``row`` under ``user``, as a request would."""

    with actor_context(user):
        return type(row)._default_manager.get(pk=row.pk)


def _posts() -> list[str]:
    messages = Message._base_manager.filter(subtype__key="activity_done").order_by("pk")
    return [Part._base_manager.select_related("fragment").get(message=message).fragment.text for message in messages]


def test_record_tasks_answer_to_their_records_readers_and_writers() -> None:
    writer, reader, stranger = _user("record-writer"), _user("record-reader"), _user("record-stranger")
    doc = _doc()
    _grant(doc, "writer", writer)
    _grant(doc, "reader", reader)

    with actor_context(writer):
        task = Task.objects.schedule(_as(writer, doc), title="Chase the supplier")
    assert task.assignee_id == writer.pk
    assert task.subject_object_id == doc.pk
    assert task.subject_content_type_id == ContentType.objects.get_for_model(doc).pk
    with actor_context(reader):
        assert list(Task.objects.for_record(_as(reader, doc))) == [task]
        with pytest.raises(PermissionDenied):
            Task.objects.schedule(_as(reader, doc), title="Not allowed")
        with pytest.raises(PermissionDenied):
            _as(reader, task).complete()
    with actor_context(stranger):
        assert list(Task.objects.for_record(doc)) == []
        assert not Task.objects.filter(pk=task.pk).exists()


def test_a_records_reader_reads_its_tasks_subject_pointer_without_loading_the_edge() -> None:
    owner, reader = _user("pointer-owner"), _user("pointer-reader")
    doc = _doc("Supplier memo")
    _grant(doc, "writer", owner)
    _grant(doc, "reader", reader)
    with actor_context(owner):
        task = Task.objects.schedule(_as(owner, doc), title="Answer the memo")
    with actor_context(reader):
        (row,) = Task.objects.for_record(_as(reader, doc))
        with CaptureQueriesContext(connection) as queries:
            reference = row.record_ref
    assert queries.captured_queries == []
    assert row.pk == task.pk
    assert (reference.model_label, reference.public_id) == ("chatterdemo.ChatterDoc", doc.sqid)


def test_a_record_tasks_own_grants_reach_nothing_past_its_record() -> None:
    """A direct grant, an assignment, ownership and a project reach a record task only with the record."""

    writer = _user("unbound-writer")
    grantee, assignee, owner, member = (_user(f"unbound-{name}") for name in ("grantee", "assignee", "owner", "member"))
    doc = _doc()
    _grant(doc, "writer", writer)
    with system_context(reason="test.record_tasks.unbound"):
        task = Task.objects.schedule(doc, title="Kept on the record", assignee=assignee)
        project = Project.objects.create(title="Elsewhere")
        Task._base_manager.filter(pk=task.pk).update(owner=owner, project=project)
    _grant(task, "reader", grantee)
    _grant(project, "reader", member)

    for user in (grantee, assignee, owner, member):
        with actor_context(user):
            assert not Task.objects.filter(pk=task.pk).exists(), user
        assert not Task._base_manager.get(pk=task.pk).with_actor(user).has_access("read"), user
    assert not Task._base_manager.get(pk=task.pk).with_actor(owner).has_access("transfer")
    with actor_context(writer):
        assert Task.objects.filter(pk=task.pk).exists()
    for permission in ("write", "transfer", "widen"):
        assert Task._base_manager.get(pk=task.pk).with_actor(writer).has_access(permission), permission


def test_a_task_takes_record_tasks_under_its_own_grants() -> None:
    owner, watcher, stranger = _user("subject-owner"), _user("subject-watcher"), _user("subject-stranger")
    with actor_context(owner):
        concern = Task.objects.create(title="Supplier concern")
    _grant(concern, "reader", watcher)

    with actor_context(owner):
        follow_up = Task.objects.schedule(_as(owner, concern), title="Call the supplier")
    with actor_context(watcher):
        assert list(Task.objects.for_record(_as(watcher, concern))) == [follow_up]
        with pytest.raises(PermissionDenied):
            _as(watcher, follow_up).complete()
    with actor_context(stranger):
        assert not Task.objects.filter(pk=follow_up.pk).exists()
    with actor_context(owner):
        with pytest.raises(ValidationError, match="takes no record tasks of its own"):
            Task.objects.schedule(_as(owner, follow_up), title="Nested")
        _as(owner, follow_up).complete()
    assert _posts() == ["Done: Call the supplier"]


def test_trash_withholds_record_tasks_from_readers_but_keeps_removal_authority() -> None:
    """The subject's trash policy governs its tasks; deleters can inspect and restore."""

    owner, reader = _user("concealed-owner"), _user("concealed-reader")
    with system_context(reason="test.record_tasks.concealed"):
        queue = Queue.objects.create(name="Concerns", key="CN", owner=owner)
        concern = Task.objects.create(title="Concealed concern", queue=queue, owner=owner)
        follow_up = Task.objects.schedule(concern, title="Call about it", assignee=owner)
    _grant(concern, "reader", reader)
    with actor_context(reader):
        assert Task.objects.filter(pk=follow_up.pk).exists()
    with actor_context(owner):
        concern.with_actor(owner).trash(reason="Removed")
        assert Task.objects.filter(pk=follow_up.pk).exists()
    with actor_context(reader):
        assert not Task.objects.filter(pk=follow_up.pk).exists()
        with pytest.raises(PermissionDenied):
            Task.objects.schedule(concern.with_actor(reader), title="After concealing")


def test_a_record_task_is_assigned_only_to_someone_who_reads_the_record() -> None:
    writer, outsider = _user("record-assigner"), _user("record-outsider")
    doc = _doc()
    _grant(doc, "writer", writer)

    with actor_context(writer), pytest.raises(RecordAccessSubjectRefused):
        Task.objects.schedule(_as(writer, doc), title="Hand it over", assignee=outsider)
    assert not Task.objects.system_context(reason="test.record_tasks.none").exists()


def test_a_record_writer_completes_and_drops_tasks_they_neither_own_nor_hold() -> None:
    admin = create_platform_admin("record-admin")
    writer = _user("record-closer")
    doc = _doc()
    _grant(doc, "writer", writer)
    with actor_context(admin):
        first = Task.objects.schedule(_as(admin, doc), title="Confirm the bank change", activity_type="call")
        second = Task.objects.schedule(_as(admin, doc), title="Obsolete follow-up")
    assert first.assignee_id == admin.pk

    with actor_context(writer):
        _as(writer, first).complete(feedback="Confirmed by phone.")
        _as(writer, second).drop("obsolete")

    first.refresh_from_db()
    second.refresh_from_db()
    assert (first.status, second.status, second.dropped_reason) == ("done", "dropped", "obsolete")
    (message,) = Message._base_manager.filter(subtype__key="activity_done")
    assert message.created_by_id == writer.pk
    assert _posts() == ["Call done: Confirm the bank change\n\nConfirmed by phone."]


def test_a_record_task_completes_once_and_closes_only_while_open() -> None:
    writer = _user("record-finisher")
    doc = _doc()
    _grant(doc, "writer", writer)
    with actor_context(writer):
        record = _as(writer, doc)
        sent = Task.objects.schedule(record, title="Send the contract")
        stale = Task.objects.schedule(record, title="Old reminder")
        concurrent = _as(writer, sent)
        _as(writer, sent).complete(feedback="Sent.")
        concurrent.complete(feedback="Sent twice.")
        _as(writer, stale).drop("obsolete")
        with pytest.raises(ValidationError, match="Reopen this record task first"):
            _as(writer, stale).complete()
        with pytest.raises(ValidationError, match="Reopen this record task first"):
            _as(writer, sent).drop("obsolete")
        _as(writer, sent).reopen()
        _as(writer, sent).complete()

    assert _posts() == ["Done: Send the contract\n\nSent.", "Done: Send the contract"]


def test_a_logged_exchange_is_a_done_task_posted_on_the_record_with_no_chatter_of_its_own() -> None:
    author = _user("record-logger")
    doc = _doc("Logged case")
    _grant(doc, "writer", author)

    with actor_context(author):
        task = Task.objects.log(
            _as(author, doc), activity_type="call", occurred_on=date(2026, 9, 1),
            note="Walked through the export.\nAll good.",
        )

    task.refresh_from_db()
    assert (task.status, task.title, task.due_date) == ("done", "Walked through the export.", date(2026, 9, 1))
    assert (task.assignee_id, task.activity_type_id, task.note) == (
        author.pk, "call", "Walked through the export.\nAll good.",
    )
    assert _posts() == ["Call done: Walked through the export.\n\nAll good."]
    own = ContentType.objects.get_for_model(Task)
    assert not ThreadAttachment._base_manager.filter(content_type=own, object_id=task.pk).exists()
    with actor_context(author):
        _as(author, task).complete()
        _as(author, task).reopen()
    assert len(_posts()) == 1
    assert not ThreadAttachment._base_manager.filter(content_type=own, object_id=task.pk).exists()


def test_a_records_tasks_list_open_first_then_by_due_date_and_title() -> None:
    writer = _user("record-planner")
    doc = _doc()
    _grant(doc, "writer", writer)
    with actor_context(writer):
        record = _as(writer, doc)
        Task.objects.schedule(record, title="Later", due_date=date(2026, 5, 1))
        Task.objects.schedule(record, title="Sooner", due_date=date(2026, 4, 1))
        Task.objects.schedule(record, title="Also sooner", due_date=date(2026, 4, 1))
        Task.objects.schedule(record, title="Finished", due_date=date(2026, 1, 1)).complete()
        everything = [task.title for task in Task.objects.for_record(record)]
        planned = [task.title for task in Task.objects.for_record(record, include_done=False)]

    assert everything == ["Also sooner", "Sooner", "Later", "Finished"]
    assert planned == ["Also sooner", "Sooner", "Later"]


# The cutover, applied to a released schema.

LONG_SUMMARY = "Reconcile " + "the supplier statement " * 10 + "and close it"  # Over a title, within a summary.


def _add(state: ProjectState, app_label: str, name: str, fields: list, **options: Any) -> None:
    options = {"db_table": f"test_released_{app_label}_{name.lower()}", **options}
    state.add_model(ModelState(app_label, name, fields, options=options))


def _released(*, owners: bool = True) -> ProjectState:
    state = ProjectState()
    key = ("id", models.BigAutoField(primary_key=True))
    account = models.ForeignKey("projects.account", models.SET_NULL, null=True, related_name="+")
    _add(state, "projects", "Account", [key])
    _add(state, "resources", "Resource", [
        ("id", models.BigAutoField(primary_key=True)),
        ("source_addon", models.CharField(max_length=200)),
        ("source_path", models.CharField(max_length=300)),
        ("xref", models.CharField(max_length=120)),
        ("target_model", models.CharField(max_length=120)),
    ])
    # The installed content-type table, which the released attachments name their records through.
    state.add_model(ModelState("contenttypes", "ContentType", [
        ("id", models.AutoField(primary_key=True)),
        ("app_label", models.CharField(max_length=100)),
        ("model", models.CharField(max_length=100)),
    ], options={"db_table": "django_content_type"}))
    _add(state, "messaging", "ThreadAttachment", [
        ("id", models.BigAutoField(primary_key=True)),
        ("content_type", models.ForeignKey("contenttypes.contenttype", models.CASCADE, null=True, related_name="+")),
        ("object_id", models.CharField(max_length=64, default="")),
    ])
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
        ("metadata", models.JSONField(blank=True, default=dict)),
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
        # Released histories before task owners lack the column.
        *([("owner", account.clone())] if owners else []),
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
        ("created_at", models.DateTimeField(auto_now_add=True, null=True)),
        ("updated_at", models.DateTimeField(auto_now=True, null=True)),
        ("converted_from_activity", models.ForeignKey(
            "messaging.threadactivity", models.SET_NULL, null=True, related_name="+",
        )),
    ], constraints=[models.UniqueConstraint(fields=("converted_from_activity",),
                                            name="uq_projects_task_converted_activity")],
         # Released models order by fields a migration's state lacks.
         ordering=("sqid",))
    _add(state, "projects", "HistoricalTask", [
        ("history_id", models.AutoField(primary_key=True)),
        ("id", models.BigIntegerField(db_index=True)),
        ("title", models.CharField(max_length=240)),
        ("status", models.CharField(max_length=32, default="open")),
        ("created_at", models.DateTimeField(null=True)),
        ("converted_from_activity", models.ForeignKey(
            "messaging.threadactivity", models.DO_NOTHING, null=True, db_constraint=False, related_name="+",
        )),
        ("history_date", models.DateTimeField()),
        ("history_change_reason", models.CharField(max_length=100, null=True)),
        ("history_type", models.CharField(max_length=1)),
        ("history_user", account.clone()),
    ])
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
                "test_released_projects_historicaltask", "test_released_projects_task",
                "test_released_messaging_threadactivity", "projects_activitytype",
                "test_released_messaging_activitytype", "test_released_messaging_threadattachment",
                "test_released_projects_account", "test_released_resources_resource",
            ):
                if table in existing:
                    cursor.execute(f"DROP TABLE {connection.ops.quote_name(table)}")


def _create(registry: Any, editor: Any) -> None:
    """Create the released tables; the content-type table is the installed one."""

    for model in registry.get_models():
        if model._meta.db_table != "django_content_type":
            editor.create_model(model)


def _seed(registry: Any, record_model: type[models.Model] = ChatterDoc) -> dict[str, Any]:
    """Create the released rows the cutover converts, about records of ``record_model``."""

    account = registry.get_model("projects", "Account")._base_manager.create()
    attachment = registry.get_model("messaging", "ThreadAttachment")._base_manager.create(
        content_type_id=ContentType.objects.get_for_model(record_model).pk, object_id="1",
    )
    kinds = registry.get_model("messaging", "ActivityType")._base_manager
    kinds.create(key="todo", name="To do")
    call = kinds.create(key="call", name="Call", glyph="phone")
    activities = registry.get_model("messaging", "ThreadActivity")._base_manager
    promoted = activities.create(attachment=attachment, user=account, activity_type_id="todo", summary="Chase",
                                 due_date=date(2026, 3, 1), created_by=account)
    logged_at = datetime(2026, 2, 1, 9, 30, tzinfo=UTC)
    activities.create(attachment=attachment, user=account, activity_type_id="call", summary="Call",
                      note="Walked through.", status="done", feedback="Agreed.", created_by=account,
                      created_at=logged_at, updated_at=logged_at)
    activities.create(attachment=attachment, user=account, activity_type_id="todo", summary="Stale",
                      status="canceled")
    activities.create(attachment=attachment, user=account, activity_type_id="todo", summary=LONG_SUMMARY)
    planned = activities.create(attachment=attachment, user=account, activity_type_id="call", summary="Planned")
    task_model = registry.get_model("projects", "Task")
    task_model._base_manager.create(
        title="Promoted", sort_order=1024.0, sub_sort_order=1024.0, converted_from_activity=promoted,
    )
    task_model._base_manager.create(
        title="Project work", project=7, sort_order=1024.0, sub_sort_order=2048.0, converted_from_activity=planned,
    )
    ledgers = registry.get_model("resources", "Resource")._base_manager
    ledgers.create(source_addon="angee.messaging", source_path="resources/master/010_messaging.activitytype.yaml",
                   xref="todo", target_model="messaging.ActivityType")
    ledgers.create(source_addon="example.crm", source_path="resources/master/035_crm.activitytype.yaml",
                   xref="call", target_model="messaging.ActivityType")
    ledgers.create(source_addon="angee.messaging", source_path="resources/demo/010_messaging.message.yaml",
                   xref="other", target_model="messaging.Message")
    return {"account": account, "attachment": attachment, "call": call, "logged_at": logged_at}


@pytest.mark.parametrize("owners", [True, False], ids=["owners", "before-owners"])
def test_released_activities_become_record_tasks_and_messaging_retires_them(owners: bool) -> None:
    released = _released(owners=owners)
    assert record_tasks.applies(released)
    registry = released.apps
    started = timezone.now()
    with _released_tables(released):
        with connection.schema_editor() as editor:
            _create(registry, editor)
            seeded = _seed(registry)
            upgraded = record_tasks.Migration("0099_record_tasks", "projects").apply(released, editor)

        attachment, account = seeded["attachment"], seeded["account"]
        assert ("messaging", "threadactivity") not in upgraded.models
        assert ("messaging", "activitytype") not in upgraded.models
        assert "converted_from_activity" not in upgraded.models["projects", "task"].fields
        assert "converted_from_activity" not in upgraded.models["projects", "historicaltask"].fields
        assert not record_tasks.applies(upgraded)
        moved = upgraded.apps.get_model("projects", "ActivityType")._base_manager.get(key="call")
        assert (moved.pk, moved.glyph) == (seeded["call"].pk, "phone")

        tasks = list(upgraded.apps.get_model("projects", "Task")._base_manager.order_by("pk"))
        long_title = LONG_SUMMARY[:239] + "…"
        assert [(task.title, task.status, task.activity_type_id, task.subject_id) for task in tasks] == [
            ("Promoted", "open", "todo", attachment.pk),
            # Project work keeps its own access: typed by its activity, not attached to the record.
            ("Project work", "open", "call", None),
            ("Call", "done", "call", attachment.pk),
            ("Stale", "dropped", "todo", attachment.pk),
            (long_title, "open", "todo", attachment.pk),
        ]
        promoted, _project_work, logged, stale, long = tasks
        assert logged.assignee_id == account.pk
        assert getattr(logged, "owner_id", account.pk) == account.pk
        assert logged.note == "Walked through.\n\nAgreed."
        assert logged.created_at == logged.updated_at == seeded["logged_at"]
        assert stale.dropped_reason == "obsolete"
        assert long.note == LONG_SUMMARY
        assert len({task.sort_order for task in (promoted, logged, stale, long)}) == 4

        history = upgraded.apps.get_model("projects", "HistoricalTask")._base_manager.order_by("id")
        assert [(row.id, row.history_type, row.subject_id) for row in history] == [
            (promoted.pk, "~", attachment.pk),
            (logged.pk, "+", attachment.pk), (stale.pk, "+", attachment.pk), (long.pk, "+", attachment.pk),
        ]
        # Each row records the state the conversion leaves, so it is dated at the conversion.
        assert all(row.history_date >= started for row in history)

        ledgers = upgraded.apps.get_model("resources", "Resource")._base_manager
        assert sorted(ledgers.values_list("xref", "source_addon", "source_path", "target_model")) == [
            ("call", "example.crm", "resources/master/035_crm.activitytype.yaml", "projects.ActivityType"),
            ("other", "angee.messaging", "resources/demo/010_messaging.message.yaml", "messaging.Message"),
            ("todo", "angee.projects", "resources/master/010_projects.activitytype.yaml", "projects.ActivityType"),
        ]
        with connection.cursor() as cursor:
            tables = set(connection.introspection.table_names(cursor))
        assert "test_released_messaging_threadactivity" not in tables
        assert promoted.pk


def test_a_stack_without_projects_drops_its_seeded_catalog_and_refuses_live_activities() -> None:
    released = _released()
    for name in ("task", "historicaltask"):
        released.remove_model("projects", name)
    current = released.clone()
    for name in ("threadactivity", "activitytype"):
        current.remove_model("messaging", name)
    graph = MigrationGraph()
    graph.add_node(("messaging", "0001_initial"), migrations.Migration("0001_initial", "messaging"))
    registry = released.apps
    with _released_tables(released):
        with connection.schema_editor() as editor:
            _create(registry, editor)
        todo = registry.get_model("messaging", "ActivityType")._base_manager.create(key="todo", name="To do")
        codec = SqidField(real_field_name="id", min_length=8, prefix="act")
        seed_ledger("messaging.ActivityType", codec.public_id_from_value(todo.pk), addon="angee.messaging", xref="todo")

        changes = DropGuardAutodetector(released, current).changes(graph=graph)
        deleted = {operation.name.lower() for migration in changes["messaging"] for operation in migration.operations
                   if isinstance(operation, migrations.DeleteModel)}
        assert deleted == {"threadactivity", "activitytype"}

        account = registry.get_model("projects", "Account")._base_manager.create()
        attachment = registry.get_model("messaging", "ThreadAttachment")._base_manager.create()
        registry.get_model("messaging", "ThreadActivity")._base_manager.create(
            attachment=attachment, user=account, activity_type_id="todo", summary="Live",
        )
        # The activity's own columns or its table refuse first; either way nothing is written.
        with pytest.raises(CommandError, match=r"unsafe autodetected (column|table) drop: messaging\.threadactivity"):
            DropGuardAutodetector(released, current).changes(graph=graph)


class PlanRan(Exception):
    """Raised in place of applying a plan the guard let through."""


def test_migrate_refuses_a_plan_applying_the_cutover_to_record_types_that_take_no_tasks(monkeypatch) -> None:
    """Django's ``migrate`` dispatches projects' guard, which refuses before the plan runs."""

    released = _released()
    registry = released.apps
    plan: list[tuple[migrations.Migration, bool]] = []
    # The released database and its plan; applying any plan stops the run.
    monkeypatch.setattr(MigrationExecutor, "migration_plan", lambda self, targets, clean_start=False: plan)
    monkeypatch.setattr(MigrationExecutor, "_create_project_state", lambda self, **kwargs: released)

    def apply(self: MigrationExecutor, targets: Any, plan: Any = None, **kwargs: Any) -> None:
        raise PlanRan(plan)

    monkeypatch.setattr(MigrationExecutor, "migrate", apply)
    cutover = record_tasks.Migration("0099_record_tasks", "projects")
    # The origin `angee build` records on the materialized cutover; another name keeps it.
    cutover.angee_origin = "angee.projects:record_tasks"
    with _released_tables(released):
        with connection.schema_editor() as editor:
            _create(registry, editor)
        _seed(registry, TrackedRecordParent)
        # A plan without the cutover is not the guard's to check.
        plan[:] = [(record_tasks.Migration("0099_record_tasks", "projects"), False)]
        with pytest.raises(PlanRan):
            call_command("migrate", skip_checks=True, interactive=False, verbosity=0)
        plan[:] = [(cutover, False)]
        with pytest.raises(CommandError, match=r"chatterdemo\.trackedrecordparent \(5 activities, 1 assignees\)"):
            call_command("migrate", skip_checks=True, interactive=False, verbosity=0)
        # The same activities on a record type that takes tasks pass.
        registry.get_model("messaging", "ThreadAttachment")._base_manager.update(
            content_type_id=ContentType.objects.get_for_model(ChatterDoc).pk,
        )
        with pytest.raises(PlanRan):
            call_command("migrate", skip_checks=True, interactive=False, verbosity=0)


def test_the_cutover_guard_is_connected_and_checked(monkeypatch) -> None:
    assert check_cutover_guard() == []
    monkeypatch.setattr(pre_migrate, "receivers", [
        receiver for receiver in pre_migrate.receivers if receiver[0][0] != GUARD_DISPATCH_UID
    ])
    assert [error.id for error in check_cutover_guard()] == ["projects.E001"]


def test_record_types_take_tasks_when_both_record_arms_reach_them() -> None:
    declared = record_task_types()
    assert {model_resource_type(ChatterDoc), model_resource_type(Task), model_resource_type(Project)} <= declared
    # Grantee types and undeclared record types take none.
    assert not {"auth/user", model_resource_type(TrackedRecordParent)} & declared


def test_the_cutover_refuses_activity_metadata_a_task_cannot_hold() -> None:
    released = _released()
    registry = released.apps
    refused = pytest.raises(RuntimeError, match="1 record activities carry metadata")
    with _released_tables(released):
        with refused, connection.schema_editor() as editor:
            _create(registry, editor)
            seeded = _seed(registry)
            registry.get_model("messaging", "ThreadActivity")._base_manager.create(
                attachment=seeded["attachment"], user=seeded["account"], activity_type_id="todo", summary="Imported",
                metadata={"source": "import"},
            )
            record_tasks.Migration("0099_record_tasks", "projects").apply(released, editor)


def test_the_cutover_refuses_a_half_migrated_history() -> None:
    released = _released()
    _add(released, "projects", "ActivityType", [("id", models.BigAutoField(primary_key=True))])
    with pytest.raises(RuntimeError, match="half-migrated"):
        record_tasks.applies(released)
