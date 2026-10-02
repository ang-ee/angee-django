"""Task insertion retains the projects owner's additional project-write policy."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
import strawberry
import strawberry_django
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection, models
from rebac import (
    PermissionDenied,
    RelationshipTuple,
    actor_context,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.actors import is_sudo
from rebac.backends import LocalBackend, backend, reset_backend
from rebac.schema import parse_zed

from angee.graphql.data.hasura import AngeeHasuraWriteBackend, hasura_model_resource, public_pk_decoder
from angee.graphql.node import AngeeNode
from angee.projects.models import Task as AbstractTask
from angee.testing.permissions import install_permission_schema
from tests.conftest import (
    create_platform_admin,
    create_user,
    execute_schema,
    result_data,
)
from tests.scopedemo.models import Scope


class ProjectAccessTask(AbstractTask):
    """Production task lifecycle with a small, independently protected project."""

    sqid_prefix = "pat_"
    owner_container = None
    project = models.ForeignKey(Scope, null=True, blank=True, on_delete=models.SET_NULL)
    milestone = None
    milestone_id = None
    converted_from_activity = None
    links = None
    file_attachments = None
    knowledge_bindings = None
    thread_attachments = None
    thread_create_log = False
    thread_create_autofollow_author = False
    thread_tracking_fields = ()

    class Meta(AbstractTask.Meta):
        abstract = False
        app_label = "scopedemo"
        rebac_resource_type = "scopedemo/project_access_task"
        constraints = [
            constraint
            for constraint in AbstractTask.Meta.constraints
            if constraint.name != "uq_projects_task_converted_activity"
        ]


@strawberry_django.type(ProjectAccessTask)
class ProjectAccessTaskType(AngeeNode):
    title: strawberry.auto
    priority_rank: int = strawberry_django.field(resolver=AbstractTask.priority_rank)
    promoted_phase: str | None = strawberry_django.field(resolver=AbstractTask.promoted_phase)


class TaskPromotion(models.Model):
    """Minimal relation target for the production task's phase expression."""

    converted_from = models.ForeignKey(ProjectAccessTask, on_delete=models.CASCADE, related_name="promoted_projects")
    current_milestone = models.ForeignKey(Scope, null=True, on_delete=models.SET_NULL)

    class Meta:
        app_label = "scopedemo"


_TASK_RESOURCE = hasura_model_resource(
    ProjectAccessTaskType,
    model=ProjectAccessTask,
    name="project_access_tasks",
    filterable=["id", "title"],
    sortable=["title"],
    aggregatable=["id"],
    insertable=["title", "project", "priority"],
    updatable=["title", "priority"],
    field_id_decode={"project": public_pk_decoder(Scope)},
    write_backend=AngeeHasuraWriteBackend(ProjectAccessTask, public_id_fields=("project",)),
    delete=False,
)
_TASK_SCHEMA = strawberry.Schema(query=_TASK_RESOURCE.query, mutation=_TASK_RESOURCE.mutation)
_TASK_INSERT = """
mutation CreateTask($project: ID!) {
  insert_project_access_tasks_one(object: {title: "Protected project task", project: $project}) { id }
}
"""


@pytest.fixture
def task_create_case(transactional_db: None) -> Iterator[tuple[Scope, Any, Any]]:
    """Keep broad row creation permission distinct from the project's write policy."""

    del transactional_db
    call_command("rebac", "sync", verbosity=0)
    active = backend()
    assert isinstance(active, LocalBackend)
    extra = parse_zed(
        """
        definition scopedemo/project_access_task {
            relation owner: auth/user // rebac:field=owner
            permission create = authenticated
            permission read = authenticated
            permission write = authenticated
            permission narrow = write
            permission widen = owner
            permission read_promoted_phase = owner
            permission transfer = owner
            permission write__owner = transfer
        }
        """
    )
    install_permission_schema(
        replace(active.schema(), definitions=[*active.schema().definitions, *extra.definitions]),
    )
    try:
        reader = create_user("task-project-reader")
        admin = create_platform_admin("task-project-admin")
        with system_context(reason="tests.task_create_access.project"):
            scope = Scope.objects.create(name="Protected project")
        write_relationships([RelationshipTuple(to_object_ref(scope), "direct_member", to_subject_ref(reader))])
        assert scope.with_actor(reader).has_access("read")
        assert not scope.with_actor(reader).has_access("write")
        yield scope, reader, admin
    finally:
        reset_backend()


@pytest.mark.parametrize("allowed", (False, True), ids=("project-reader", "project-writer"))
def test_graphql_task_create_requires_project_write(
    task_create_case: tuple[Scope, Any, Any],
    allowed: bool,
) -> None:
    """Resolving a readable project cannot authorize adding a task to it."""

    project, reader, admin = task_create_case
    result = execute_schema(
        _TASK_SCHEMA,
        _TASK_INSERT,
        {"project": project.public_id},
        user=admin if allowed else reader,
    )

    if allowed:
        public_id = result_data(result)["insert_project_access_tasks_one"]["id"]
        task = ProjectAccessTask._base_manager.get(sqid=public_id)
        assert task.project_id == project.pk
        assert ProjectAccessTask._base_manager.count() == 1
        assert ProjectAccessTask.history.count() == 1
    else:
        assert result.errors
        assert isinstance(result.errors[0].original_error, PermissionDenied)
        assert "Write access to the project is required to add a task." in result.errors[0].message
        assert ProjectAccessTask._base_manager.count() == 0
        assert ProjectAccessTask.history.count() == 0


def test_graphql_standalone_task_create_does_not_require_project_write(
    task_create_case: tuple[Scope, Any, Any],
) -> None:
    """An ordinary project reader can create a task with no project attachment."""

    _project, reader, _admin = task_create_case
    created = result_data(
        execute_schema(
            _TASK_SCHEMA,
            """
            mutation {
              insert_project_access_tasks_one(object: {title: "Standalone task", project: null}) { id }
            }
            """,
            user=reader,
        )
    )["insert_project_access_tasks_one"]

    task = ProjectAccessTask._base_manager.get(sqid=created["id"])
    assert task.project_id is None
    assert task.created_by_id == reader.pk
    assert ProjectAccessTask._base_manager.count() == 1
    assert ProjectAccessTask.history.count() == 1


def test_task_mutations_return_unannotated_projections(
    task_create_case: tuple[Scope, Any, Any],
) -> None:
    """Insert and update responses resolve both scalars without optimizer annotations."""

    phase, reader, _admin = task_create_case
    outsider = create_user("phase-outsider")
    created = result_data(
        execute_schema(
            _TASK_SCHEMA,
            """mutation {
          insert_project_access_tasks_one(object: {title: "New task", priority: "urgent"}) {
            id priorityRank promotedPhase
          }
        }""",
            user=reader,
        )
    )["insert_project_access_tasks_one"]
    assert created["priorityRank"] == 4
    assert created["promotedPhase"] is None
    task = ProjectAccessTask._base_manager.get(sqid=created["id"])
    TaskPromotion.objects.create(converted_from=task, current_milestone=phase)
    updated = result_data(
        execute_schema(
            _TASK_SCHEMA,
            """mutation UpdateTask($id: String!) {
          update_project_access_tasks_by_pk(pk_columns: {id: $id}, _set: {priority: "low"}) {
            id priorityRank promotedPhase
          }
        }""",
            {"id": created["id"]},
            user=reader,
        )
    )["update_project_access_tasks_by_pk"]
    assert updated == {"id": created["id"], "priorityRank": 1, "promotedPhase": phase.name}
    with actor_context(outsider):
        assert task.with_actor(reader).promoted_phase() == phase.name
    with actor_context(reader):
        assert task.with_actor(outsider).promoted_phase() is None


@pytest.mark.parametrize("path", ("verb", "save_fields", "save", "update", "bulk_update"))
def test_task_visibility_has_no_unvalidated_write_path(
    task_create_case: tuple[Scope, Any, Any],
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    """A refusing hook and the write-once guard cover every visibility write API."""

    _project, writer, owner = task_create_case
    calls = []

    def refuse(row: ProjectAccessTask, value: str) -> None:
        assert row.actor() is None
        assert is_sudo()
        assert connection.in_atomic_block
        assert row.visibility == "restricted"
        calls.append(value)
        raise ValidationError({"visibility": "Transition refused."})

    monkeypatch.setattr(ProjectAccessTask, "validate_visibility", refuse)
    with system_context(reason="tests.task_visibility.insert"):
        task = ProjectAccessTask.objects.create(title="Restricted task", owner=owner, visibility="restricted")
    assert calls == []
    task.with_actor(writer)
    assert task.has_access("write")
    assert not task.has_access("widen")
    initial_revision, initial_history = task.revision, task.history.count()
    if path == "verb":
        with pytest.raises(PermissionDenied, match="Widen access"):
            task.set_visibility("inherited")
        assert calls == []
        with pytest.raises(ValidationError, match="Transition refused"):
            task.with_actor(owner).set_visibility("inherited")
        assert calls == ["inherited"]
    elif path in ("save_fields", "save"):
        task.visibility = "inherited"
        with pytest.raises(ValidationError, match="immutable"):
            task.save(update_fields=("visibility", "updated_at") if path == "save_fields" else None)
    elif path == "update":
        with pytest.raises(ValueError, match="set_visibility"):
            ProjectAccessTask.objects.with_actor(writer).filter(pk=task.pk).update(visibility="inherited")
    else:
        task.visibility = "inherited"
        with pytest.raises(ValueError, match="set_visibility"):
            ProjectAccessTask.objects.with_actor(writer).bulk_update([task], ["visibility"])
    task.refresh_from_db()
    assert task.visibility == "restricted"
    assert task.revision == initial_revision
    assert task.history.count() == initial_history


def test_task_visibility_transition_and_replay(
    task_create_case: tuple[Scope, Any, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The verb authorizes one transition; replay does not call the hook or write."""

    _project, writer, owner = task_create_case
    calls = []

    def validate(row: ProjectAccessTask, value: str) -> None:
        calls.append((row.visibility, value))
        assert row.actor() is None
        assert is_sudo()

    monkeypatch.setattr(ProjectAccessTask, "validate_visibility", validate)
    with system_context(reason="tests.task_visibility.insert"):
        task = ProjectAccessTask.objects.create(title="Task", owner=owner)
    initial_revision = task.revision
    task.with_actor(writer).set_visibility("restricted", expected_revision=initial_revision)
    assert task.visibility == "restricted"
    assert task.revision == initial_revision + 1
    task.set_visibility("restricted", expected_revision=task.revision)
    assert task.revision == initial_revision + 1
    assert calls == [("inherited", "restricted")]
    task.with_actor(owner).set_visibility("inherited", expected_revision=task.revision)
    assert task.visibility == "inherited"
    assert calls == [("inherited", "restricted"), ("restricted", "inherited")]


def test_pinned_task_insert_requires_project_write_inside_system_context(
    task_create_case: tuple[Scope, Any, Any],
) -> None:
    """The queryset's actor outranks ambient elevation at the model-owned gate."""

    project, reader, _admin = task_create_case
    candidate = ProjectAccessTask(title="Pinned reader task", project=project)
    with (
        system_context(reason="tests.task_create_access.ambient"),
        pytest.raises(
            PermissionDenied,
            match="Write access to the project is required to add a task.",
        ),
    ):
        ProjectAccessTask.objects.as_user(reader).insert(candidate)
    assert ProjectAccessTask._base_manager.count() == 0
    assert ProjectAccessTask.history.count() == 0


@pytest.mark.parametrize("allowed", (False, True), ids=("project-reader", "project-writer"))
def test_direct_task_save_requires_project_write(
    task_create_case: tuple[Scope, Any, Any],
    allowed: bool,
) -> None:
    """Native instance saves enforce the same project policy as GraphQL inserts."""

    project, reader, admin = task_create_case
    candidate = ProjectAccessTask(title="Direct task", project=project).with_actor(admin if allowed else reader)
    if allowed:
        candidate.save()
        assert ProjectAccessTask._base_manager.get(pk=candidate.pk).project_id == project.pk
    else:
        with pytest.raises(PermissionDenied, match="Write access to the project is required to add a task."):
            candidate.save()
        assert not ProjectAccessTask._base_manager.exists()


@pytest.mark.parametrize("allowed", (False, True), ids=("project-reader", "project-writer"))
@pytest.mark.parametrize("update_fields", (None, ("project",), ("project_id",)))
def test_task_move_requires_destination_project_write(
    task_create_case: tuple[Scope, Any, Any],
    allowed: bool,
    update_fields: tuple[str, ...] | None,
) -> None:
    """A task writer cannot move it into a merely readable project."""

    destination, reader, admin = task_create_case
    with system_context(reason="tests.task_create_access.move"):
        source = Scope.objects.create(name="Source project")
        task = ProjectAccessTask.objects.create(title="Moving task", project=source)
    task = ProjectAccessTask.objects.with_actor(admin if allowed else reader).get(pk=task.pk)
    assert task.has_access("write")
    initial_history = task.history.count()
    initial_revision = task.revision
    task.project = destination

    if allowed:
        task.save(update_fields=update_fields)
        task.refresh_from_db()
        assert task.project_id == destination.pk
        assert task.revision == initial_revision + 1
        assert task.history.count() == initial_history + 1
    else:
        with pytest.raises(PermissionDenied, match="Write access to the project is required to add a task."):
            task.save(update_fields=update_fields)
        task.refresh_from_db()
        assert task.project_id == source.pk
        assert task.revision == initial_revision
        assert task.history.count() == initial_history
