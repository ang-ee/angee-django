"""Productivity write behavior."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
import strawberry_django
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection, models
from django.utils import timezone
from rebac import system_context
from rebac.backends import LocalBackend, backend, reset_backend
from rebac.schema import parse_zed
from strawberry import auto

from angee.base.fields import StateField
from angee.base.mixins import AuditMixin
from angee.base.models import AngeeDataModel
from angee.base.stages import Stage as OrderedStage
from angee.base.stages import StagedModelMixin
from angee.graphql.data import hasura_model_resource
from angee.graphql.data.hasura import AngeeHasuraWriteBackend
from angee.graphql.node import AngeeNode
from angee.graphql.schema import GraphQLSchemas
from angee.intake.models import Need as AbstractNeed
from angee.projects.models import Task as AbstractTask
from angee.work.models import Queue as AbstractQueue
from angee.work.models import Stage as AbstractWorkStage
from angee.work.models import TaskWork
from tests.conftest import (
    SchemaAddon,
    _clear_model_tables,
    _create_missing_tables,
    create_platform_admin,
    execute_schema,
    result_data,
)
from tests.projects_models import Task
from tests.spaces_models import Group, Membership
from tests.test_messaging import Party, Person
from tests.test_sequence import SEQUENCE_TEST_MODELS


class RoutingStageContainer(models.Model):
    """Small native container for stage defaults and scope validation."""

    default_stage = models.ForeignKey("tests.RoutingPipelineStage", null=True, on_delete=models.SET_NULL)

    class Meta:
        app_label = "tests"


class RoutingPipelineStage(OrderedStage):
    """A stage with the production native container seam."""

    container_field_name = "container"
    container = models.ForeignKey(RoutingStageContainer, on_delete=models.CASCADE)

    class Meta:
        app_label = "tests"


class RoutingStageRecord(StagedModelMixin, models.Model):
    """A record whose stage and container IDs can both be deferred."""

    stage_container_field_name = "container"
    container = models.ForeignKey(RoutingStageContainer, on_delete=models.CASCADE)
    stage = models.ForeignKey(RoutingPipelineStage, null=True, on_delete=models.SET_NULL)

    class Meta:
        app_label = "tests"


class RoutingSnoozeRecord(models.Model):
    """Minimal native columns needed by the work-owned bulk wake operation."""

    snoozed_until = models.DateTimeField(null=True)
    snoozed_by = models.IntegerField(null=True)
    updated_at = models.DateTimeField()

    class Meta:
        app_label = "tests"


class Queue(AbstractQueue, Group):
    """Native materialized work queue, retaining Group and sequence ownership."""

    class Meta(AbstractQueue.Meta):
        abstract = False
        app_label = "work"
        db_table = "test_create_work_queue"
        rebac_resource_type = "work/queue"


class Stage(AbstractWorkStage):
    """Production stage behavior used by the prepared-instance regressions."""

    class Meta(AbstractWorkStage.Meta):
        abstract = False
        app_label = "work"
        db_table = "test_create_work_stage"
        rebac_resource_type = "work/stage"


class CreateProject(AngeeDataModel):
    """A project identity for the task-to-project projection."""

    sqid_prefix = "cpr_"

    class Meta:
        app_label = "scopedemo"
        rebac_resource_type = "tests/create_project"


class CreateTask(TaskWork, AuditMixin, AngeeDataModel):
    """Native Work donor over the lifecycle columns it projects."""

    sqid_prefix = "ctk_"
    TaskStatus = AbstractTask.TaskStatus
    TaskDroppedReason = AbstractTask.TaskDroppedReason
    title = models.CharField(max_length=240)
    status = StateField(choices_enum=AbstractTask.TaskStatus, default=AbstractTask.TaskStatus.OPEN)
    done_at = models.DateTimeField(null=True, blank=True)
    dropped_at = models.DateTimeField(null=True, blank=True)
    dropped_reason = StateField(choices_enum=AbstractTask.TaskDroppedReason, null=True, blank=True)
    project = models.ForeignKey(CreateProject, null=True, blank=True, on_delete=models.SET_NULL)
    stage = models.ForeignKey(Stage, null=True, blank=True, on_delete=models.SET_NULL)
    cycle = None
    cycle_id = None
    hasura_readable_fields = ()
    hasura_filterable_fields = ()
    hasura_sortable_fields = ()
    hasura_aggregatable_fields = ()
    hasura_groupable_fields = ()
    hasura_insertable_fields = ()
    hasura_updatable_fields = ()

    class Meta:
        app_label = "scopedemo"
        rebac_resource_type = "tests/create_task"


class CreateNeed(AbstractNeed):
    """Production target normalization with explicit test-graph relations."""

    task = models.ForeignKey(CreateTask, null=True, blank=True, on_delete=models.CASCADE)
    project = models.ForeignKey(CreateProject, null=True, blank=True, on_delete=models.CASCADE)
    original_task = models.ForeignKey(CreateTask, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    party = None
    source_message = None

    class Meta:
        app_label = "scopedemo"
        rebac_resource_type = "tests/create_need"
        constraints = AbstractNeed.Meta.constraints[:1]


@strawberry_django.type(CreateTask)
class CreateTaskType(AngeeNode):
    title: auto
    status: auto


@strawberry_django.type(CreateNeed)
class CreateNeedType(AngeeNode):
    body: auto
    targets_project: auto


@strawberry_django.type(Stage)
class CreateStageType(AngeeNode):
    name: auto
    category: auto


@pytest.fixture
def productivity_tables(transactional_db: None) -> Iterator[None]:
    """Create native stage and snooze rows for behavior regressions."""
    test_models = (RoutingStageContainer, RoutingPipelineStage, RoutingStageRecord, RoutingSnoozeRecord)
    with connection.schema_editor() as editor:
        for model in test_models:
            editor.create_model(model)
    try:
        yield
    finally:
        with connection.schema_editor() as editor:
            for model in reversed(test_models):
                editor.delete_model(model)


@pytest.fixture
def productivity_create_case(transactional_db: None) -> Iterator[tuple[Any, Any, Queue]]:
    """Expose the production donors through real Hasura resources and local REBAC."""
    del transactional_db
    model_types = (
        Party,
        Person,
        Group,
        Membership,
        Queue,
        Stage,
        *SEQUENCE_TEST_MODELS,
        CreateProject,
        CreateTask,
        CreateNeed,
    )
    created_models = _create_missing_tables(model_types)
    call_command("rebac", "sync", verbosity=0)
    active = backend()
    assert isinstance(active, LocalBackend)
    extra = parse_zed("""
        definition tests/create_project {
            permission create = authenticated
            permission read = authenticated
            permission write = authenticated
        }
        definition tests/create_task {
            permission create = authenticated
            permission read = authenticated
            permission write = authenticated
        }
        definition tests/create_need {
            relation task: tests/create_task // rebac:field=task
            relation project: tests/create_project // rebac:field=project
            permission create = task->write + project->write
            permission read = task->read + project->read
        }
        """)
    active.set_schema(replace(active.schema(), definitions=[*active.schema().definitions, *extra.definitions]))
    try:
        admin = create_platform_admin("productivity-create-admin")
        with system_context(reason="tests.productivity.create.queue"):
            queue = Queue.objects.create(key="CREATE", name="Create", slug="create")
        resources = [
            hasura_model_resource(
                node,
                model=model,
                name=name,
                filterable=["id"],
                sortable=["id"],
                aggregatable=["id"],
                insertable=fields,
                update=False,
                delete=False,
                write_backend=AngeeHasuraWriteBackend(model, public_id_fields=relations),
            )
            for node, model, name, fields, relations in (
                (
                    CreateTaskType,
                    CreateTask,
                    "create_tasks",
                    ["title", "project", "queue", "stage"],
                    ("project", "queue", "stage"),
                ),
                (CreateNeedType, CreateNeed, "create_needs", ["body", "task", "project"], ("task", "project")),
                (CreateStageType, Stage, "create_stages", ["queue", "name", "category"], ("queue",)),
            )
        ]
        schema = GraphQLSchemas(
            [
                SchemaAddon(
                    {
                        "public": {
                            "query": [resource.query for resource in resources],
                            "mutation": [resource.mutation for resource in resources],
                            "types": [
                                CreateTaskType,
                                CreateNeedType,
                                CreateStageType,
                                *(item for resource in resources for item in resource.types),
                            ],
                        }
                    }
                )
            ]
        ).build("public")
        yield (schema, admin, queue)
    finally:
        _clear_model_tables(model_types)
        if created_models:
            with connection.schema_editor() as editor:
                for model in reversed(created_models):
                    editor.delete_model(model)
        reset_backend()


def test_graphql_task_create_preserves_stage_projected_status(productivity_create_case: tuple[Any, Any, Queue]) -> None:
    """Graphql task create preserves stage projected status."""
    schema, actor, queue = productivity_create_case
    stage = Stage.objects.as_user(actor).get(queue=queue, category="completed")
    created = result_data(
        execute_schema(
            schema,
            """
            mutation CreateTask($stage: ID!) {
              insert_create_tasks_one(object: {title: "Completed on create", stage: $stage}) { id status }
            }
            """,
            {"stage": stage.sqid},
            user=actor,
        )
    )["insert_create_tasks_one"]
    assert created["status"] == "DONE"
    task = CreateTask.objects.as_user(actor).get(sqid=created["id"])
    assert task.queue_id == queue.pk
    assert task.stage_id == stage.pk
    assert task.status == AbstractTask.TaskStatus.DONE
    assert task.done_at is not None
    assert task.number == 1


def test_graphql_need_create_preserves_task_target_provenance(productivity_create_case: tuple[Any, Any, Queue]) -> None:
    """Graphql need create preserves task target provenance."""
    schema, actor, queue = productivity_create_case
    with system_context(reason="tests.productivity.create.need_target"):
        project = CreateProject.objects.create()
        task = CreateTask.objects.create(title="Target", queue=queue, project=project)
    created = result_data(
        execute_schema(
            schema,
            """
            mutation CreateNeed($task: ID!) {
              insert_create_needs_one(object: {task: $task, body: "Task request"}) { id targets_project }
            }
            """,
            {"task": task.sqid},
            user=actor,
        )
    )["insert_create_needs_one"]
    assert created["targets_project"] is False
    need = CreateNeed.objects.as_user(actor).get(sqid=created["id"])
    assert need.task_id == task.pk
    assert need.project_id == project.pk
    assert need.targets_project is False


@pytest.mark.parametrize("category", ("TRIAGE", "DUPLICATE"))
def test_graphql_stage_create_keeps_system_category_guard(
    productivity_create_case: tuple[Any, Any, Queue], category: str
) -> None:
    """Graphql stage create keeps system category guard."""
    schema, actor, queue = productivity_create_case
    with system_context(reason="tests.productivity.create.remove_reserved_stage"):
        Stage.objects.filter(queue=queue, category=category.lower()).delete()
    result = execute_schema(
        schema,
        """
        mutation CreateStage($object: create_stages_insert_input!) {
          insert_create_stages_one(object: $object) { id }
        }
        """,
        {"object": {"queue": queue.sqid, "name": "User reserved stage", "category": category}},
        user=actor,
    )
    assert result.errors is not None
    assert isinstance(result.errors[0].original_error, ValidationError)
    assert "Triage and duplicate stages are system-provisioned." in result.errors[0].message
    assert not Stage.objects.as_user(actor).filter(queue=queue, category=category.lower()).exists()


def test_noop_refresh_preserves_authored_work_state_and_loaded_queue() -> None:
    task = Task()
    task.status = "done"
    task.queue_id = 2
    task._work_loaded_queue_id = 1
    task.refresh_from_db(fields=[])
    assert task._work_status_assigned is True
    assert task._work_loaded_queue_id == 1


@pytest.mark.django_db(transaction=True)
def test_deferred_stage_default_and_validation_call_hooks(
    productivity_tables: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = RoutingStageContainer.objects.create()
    stage = RoutingPipelineStage.objects.create(container_id=container.pk, name="Ready")
    RoutingStageContainer.objects.filter(pk=container.pk).update(default_stage_id=stage.pk)
    record = RoutingStageRecord.objects.create(container_id=container.pk, stage_id=stage.pk)
    record = RoutingStageRecord.objects.only("pk").get(pk=record.pk)
    assert RoutingStageRecord.objects.filter(pk=record.pk).exists()
    original_default = RoutingPipelineStage.resolve_default
    original_stages = RoutingPipelineStage.for_container
    seen = []

    def legacy_default(cls: Any, container: Any) -> Any:
        seen.append(("default", container._state.db))
        return original_default(container)

    def legacy_stages(cls: Any, container: Any) -> Any:
        seen.append(("stages", container._state.db))
        return original_stages(container)

    monkeypatch.setattr(RoutingPipelineStage, "resolve_default", classmethod(legacy_default))
    monkeypatch.setattr(RoutingPipelineStage, "for_container", classmethod(legacy_stages))
    assert record.resolve_default_stage().pk == stage.pk
    record.validate_stage_scope()
    assert seen == [("default", "default"), ("stages", "default"), ("stages", "default")]


@pytest.mark.django_db(transaction=True)
def test_stage_scope_rejects_foreign_container(productivity_tables: None) -> None:
    first = RoutingStageContainer.objects.create()
    second = RoutingStageContainer.objects.create()
    stage = RoutingPipelineStage.objects.create(container_id=first.pk, name="Ready")
    record = RoutingStageRecord(container_id=second.pk, stage_id=stage.pk)
    with pytest.raises(ValidationError, match="record's container"):
        record.validate_stage_scope()


@pytest.mark.django_db(transaction=True)
def test_bulk_snooze_wake_clears_snooze_fields(productivity_tables: None, monkeypatch: pytest.MonkeyPatch) -> None:
    now = timezone.now()
    row = RoutingSnoozeRecord.objects.create(snoozed_until=now, snoozed_by=7, updated_at=now)
    assert TaskWork.wake_due_snoozes.__func__(RoutingSnoozeRecord, now=now) == 1
    row.refresh_from_db()
    assert row.snoozed_until is None
    assert row.snoozed_by is None
