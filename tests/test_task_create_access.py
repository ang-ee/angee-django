"""Task insertion retains the projects owner's additional project-write policy."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
import strawberry
import strawberry_django
from django.core.management import call_command
from django.db import connection, models
from rebac import (
    PermissionDenied,
    RelationshipTuple,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.backends import LocalBackend, backend, reset_backend
from rebac.schema import parse_zed

from angee.graphql.data.hasura import AngeeHasuraWriteBackend, hasura_model_resource, public_pk_decoder
from angee.graphql.node import AngeeNode
from angee.projects.models import Task as AbstractTask
from tests.conftest import (
    _clear_model_tables,
    _create_missing_tables,
    create_platform_admin,
    create_user,
    execute_schema,
    result_data,
)
from tests.scopedemo.models import Scope


class ProjectAccessTask(AbstractTask):
    """Production task lifecycle with a small, independently protected project."""

    sqid_prefix = "pat_"
    project = models.ForeignKey(Scope, null=True, blank=True, on_delete=models.SET_NULL)
    milestone = None
    milestone_id = None
    converted_from_activity = None
    links = None
    thread_attachments = None
    thread_create_log = False
    thread_create_autofollow_author = False
    thread_tracking_fields = ()

    class Meta(AbstractTask.Meta):
        abstract = False
        app_label = "scopedemo"
        rebac_resource_type = "scopedemo/project_access_task"
        constraints = AbstractTask.Meta.constraints[:2]


@strawberry_django.type(ProjectAccessTask)
class ProjectAccessTaskType(AngeeNode):
    title: strawberry.auto


_TASK_RESOURCE = hasura_model_resource(
    ProjectAccessTaskType,
    model=ProjectAccessTask,
    name="project_access_tasks",
    filterable=["id", "title"],
    sortable=["title"],
    aggregatable=["id"],
    insertable=["title", "project"],
    field_id_decode={"project": public_pk_decoder(Scope)},
    write_backend=AngeeHasuraWriteBackend(ProjectAccessTask, public_id_fields=("project",)),
    update=False,
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
    test_models = (ProjectAccessTask, ProjectAccessTask.history.model)
    created_models = _create_missing_tables(test_models)
    call_command("rebac", "sync", verbosity=0)
    active = backend()
    assert isinstance(active, LocalBackend)
    extra = parse_zed(
        """
        definition scopedemo/project_access_task {
            permission create = authenticated
            permission read = authenticated
        }
        """
    )
    active.set_schema(replace(active.schema(), definitions=[*active.schema().definitions, *extra.definitions]))
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
        _clear_model_tables(test_models)
        if created_models:
            with connection.schema_editor() as editor:
                for model in reversed(created_models):
                    editor.delete_model(model)
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


def test_pinned_task_insert_requires_project_write_inside_system_context(
    task_create_case: tuple[Scope, Any, Any],
) -> None:
    """The queryset's actor outranks ambient elevation at the model-owned gate."""

    project, reader, _admin = task_create_case
    candidate = ProjectAccessTask(title="Pinned reader task", project=project)
    with system_context(reason="tests.task_create_access.ambient"), pytest.raises(
        PermissionDenied,
        match="Write access to the project is required to add a task.",
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
