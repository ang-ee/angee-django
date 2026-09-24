"""Productivity writes preserve their alias, creation provenance, and authority."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import replace
from typing import Any

import pytest
import strawberry_django
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.management import call_command
from django.db import connection, models, router
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
from angee.portfolio.models import ProductManager, UpdateManager
from angee.projects.models import LinkManager, ProjectManager, TaskManager
from angee.projects.models import Task as AbstractTask
from angee.proposals.models import ProposalManager
from angee.work.models import Queue as AbstractQueue
from angee.work.models import QueueManager, TaskWork
from angee.work.models import Stage as AbstractWorkStage
from tests.conftest import (
    SchemaAddon,
    _clear_model_tables,
    _create_missing_tables,
    create_platform_admin,
    execute_schema,
    result_data,
)
from tests.projects_models import Task
from tests.proposals_models import Proposal, Round
from tests.spaces_models import Group, Membership
from tests.test_messaging import Party, Person
from tests.test_sequence import SEQUENCE_TEST_MODELS
from tests.test_transitions import TransitionRouter


class RoutingStageContainer(models.Model):
    """Small native container for the shared stage owner's routing contract."""

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
    # Cycle behavior is independent of constructor/status provenance.
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
def productivity_create_case(transactional_db: None) -> Iterator[tuple[Any, Any, Queue]]:
    """Expose the production donors through real Hasura resources and local REBAC."""

    del transactional_db
    # Queue access delegates to Group's memberships__party__person__user path.
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
    extra = parse_zed(
        """
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
        """
    )
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
                        },
                    }
                )
            ]
        ).build("public")
        yield schema, admin, queue
    finally:
        _clear_model_tables(model_types)
        if created_models:
            with connection.schema_editor() as editor:
                for model in reversed(created_models):
                    editor.delete_model(model)
        reset_backend()


def test_graphql_task_create_preserves_stage_projected_status(
    productivity_create_case: tuple[Any, Any, Queue],
) -> None:
    """A clean-derived done status remains distinct from caller-authored status."""

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


def test_graphql_need_create_preserves_task_target_provenance(
    productivity_create_case: tuple[Any, Any, Queue],
) -> None:
    """Task-derived project context never becomes a second authored target."""

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
    productivity_create_case: tuple[Any, Any, Queue],
    category: str,
) -> None:
    """A real admin still lacks ambient system-provisioning authority."""

    schema, actor, queue = productivity_create_case
    with system_context(reason="tests.productivity.create.remove_reserved_stage"):
        # Remove the existing provisioned category so a uniqueness error cannot
        # accidentally satisfy the guard regression.
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


@pytest.fixture
def productivity_writer(database_alias: Callable[[str], AbstractContextManager[str]]) -> Iterator[str]:
    """Expose the productivity schema through the shared connection factory."""

    test_models = (RoutingStageContainer, RoutingPipelineStage, RoutingStageRecord, RoutingSnoozeRecord)
    with connection.schema_editor() as editor:
        for model in test_models:
            editor.create_model(model)
    try:
        with database_alias("productivity_writer") as alias:
            yield alias
    finally:
        with connection.schema_editor() as editor:
            for model in reversed(test_models):
                editor.delete_model(model)


def _reject_default_query(*args: Any) -> None:
    raise AssertionError("A productivity write touched the unrelated default connection.")


def test_noop_refresh_preserves_authored_work_state_and_loaded_queue() -> None:
    task = Task()
    task.status = "done"
    task.queue_id = 2
    task._work_loaded_queue_id = 1
    task.refresh_from_db(fields=[])
    assert task._work_status_assigned is True
    assert task._work_loaded_queue_id == 1


def test_stage_default_requires_caller_to_pin_container() -> None:
    """A mismatched alias fails before mutating or querying the caller's object."""

    container = RoutingStageContainer(default_stage_id=1)
    container._state.db = "original"
    with pytest.raises(ValueError, match="Pin the stage container"):
        RoutingPipelineStage.resolve_default(container, using="other")
    assert container._state.db == "original"
    assert container.default_stage_id == 1


@pytest.mark.django_db(transaction=True)
def test_stage_default_and_validation_use_selected_alias_with_legacy_hooks(
    productivity_writer: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = RoutingStageContainer.objects.create()
    stage = RoutingPipelineStage.objects.create(container_id=container.pk, name="Ready")
    RoutingStageContainer.objects.filter(pk=container.pk).update(default_stage_id=stage.pk)
    record = RoutingStageRecord.objects.create(container_id=container.pk, stage_id=stage.pk)
    record = RoutingStageRecord.objects.using("default").only("pk").get(pk=record.pk)
    assert RoutingStageRecord.objects.using(productivity_writer).filter(pk=record.pk).exists()
    routing = TransitionRouter("unavailable-writer")
    monkeypatch.setattr(router, "routers", [routing])
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
    with connection.execute_wrapper(_reject_default_query):
        assert record.resolve_default_stage(using=productivity_writer).pk == stage.pk
        record.validate_stage_scope(using=productivity_writer)
    assert seen == [
        ("default", productivity_writer),
        ("stages", productivity_writer),
        ("stages", productivity_writer),
    ]
    assert routing.writes == []


@pytest.mark.django_db(transaction=True)
def test_stage_scope_still_rejects_a_foreign_container_on_selected_alias(productivity_writer: str) -> None:
    first = RoutingStageContainer.objects.using(productivity_writer).create()
    second = RoutingStageContainer.objects.using(productivity_writer).create()
    stage = RoutingPipelineStage.objects.using(productivity_writer).create(container_id=first.pk, name="Ready")
    record = RoutingStageRecord(container_id=second.pk, stage_id=stage.pk)
    with connection.execute_wrapper(_reject_default_query), pytest.raises(ValidationError, match="record's container"):
        record.validate_stage_scope(using=productivity_writer)


@pytest.mark.django_db(transaction=True)
def test_bulk_snooze_wake_reads_and_writes_only_the_selected_connection(
    productivity_writer: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = timezone.now()
    row = RoutingSnoozeRecord.objects.using(productivity_writer).create(
        snoozed_until=now, snoozed_by=7, updated_at=now
    )
    monkeypatch.setattr(router, "routers", [TransitionRouter("unavailable-writer")])
    with connection.execute_wrapper(_reject_default_query):
        assert TaskWork.wake_due_snoozes.__func__(RoutingSnoozeRecord, now=now, using=productivity_writer) == 1
    row.refresh_from_db(using=productivity_writer)
    assert row.snoozed_until is None
    assert row.snoozed_by is None


@pytest.mark.parametrize(
    ("manager_type", "method", "args", "kwargs"),
    [
        (ProjectManager, "from_task", (None,), {}),
        (TaskManager, "from_activity", (None,), {}),
        (LinkManager, "upsert", (), {"target": None, "url": "https://example.test"}),
        (ProductManager, "from_project", (None,), {}),
        (UpdateManager, "report", (), {"target": None, "health": "on_track"}),
        (ProposalManager, "capture_from_message", (None, None), {}),
        (QueueManager, "provision_personal", (None,), {}),
    ],
)
def test_unsupported_manager_operations_reject_before_queries_or_argument_reads(
    manager_type: Any, method: str, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> None:
    manager = manager_type()
    manager.model = RoutingStageContainer
    with pytest.raises(ImproperlyConfigured, match="default authorization database"):
        getattr(manager.db_manager("unavailable-writer"), method)(*args, **kwargs)


@pytest.mark.parametrize(
    ("model", "method", "args"),
    [
        (Round, "open", ()),
        (Round, "close", ("no_award",)),
        (Round, "transfer_facilitation", (None,)),
        (Proposal, "save", ()),
        (Proposal, "submit", ()),
        (Proposal, "withdraw", ()),
        (Proposal, "identify_party", (None,)),
        (Proposal, "create_track", ()),
        (Proposal, "publish_track", ()),
    ],
)
def test_proposal_tuple_operations_reject_before_persistence(model: Any, method: str, args: tuple[Any, ...]) -> None:
    instance = model()
    with pytest.raises(ImproperlyConfigured, match="default authorization database"):
        getattr(instance, method)(*args, using="unavailable-writer")
