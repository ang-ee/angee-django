"""Productivity write behavior."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import strawberry_django
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from rebac import system_context
from strawberry import auto

import tests.test_messaging  # noqa: F401 -- register related models before native database setup
from angee.base.models.stages import Stage as OrderedStage
from angee.base.models.stages import StagedModelMixin
from angee.graphql.data.hasura import AngeeHasuraWriteBackend, hasura_model_resource
from angee.graphql.node import AngeeNode
from angee.graphql.schema import GraphQLSchemas
from angee.projects.testing.models import Queue, Stage, Task
from tests import test_sequence  # noqa: F401 -- register Queue's sequence target before database setup
from tests.composed_host import run_composed_tests
from tests.conftest import (
    SchemaAddon,
    create_platform_admin,
    execute_schema,
    result_data,
)
from tests.tables import model_tables


def test_composed_projects_schema_with_work(tmp_path: Path) -> None:
    """Work's sortable relation path and phase mapping must survive schema import."""
    run_composed_tests(tmp_path, "tests.test_work_task_access.SchemaImportTests", app="angee.work")


def test_duplicate_merge_uses_live_link_backing(tmp_path: Path) -> None:
    """Merged links follow their new target without writing relationship rows."""
    run_composed_tests(tmp_path, "tests.test_work_task_access.DuplicateLinkTests", app="angee.work")


class RoutingStageContainer(models.Model):
    """Small native container for stage defaults and scope validation."""

    default_stage = models.ForeignKey("tests.RoutingPipelineStage", null=True, on_delete=models.SET_NULL)

    class Meta:
        app_label = "tests"


class RoutingPipelineStage(OrderedStage):
    """A stage with the production native container seam."""

    container_field_name = "container"
    container = models.ForeignKey(RoutingStageContainer, on_delete=models.CASCADE)

    class Meta(OrderedStage.Meta):
        abstract = False
        app_label = "tests"
        constraints = [models.UniqueConstraint(
            fields=("container", "position"), name="routing_stage_container_position",
        )]


class RoutingStageRecord(StagedModelMixin[RoutingPipelineStage], models.Model):
    """A record whose stage and container IDs can both be deferred."""

    stage_container_field_name = "container"
    container = models.ForeignKey(RoutingStageContainer, on_delete=models.CASCADE)
    stage = models.ForeignKey(RoutingPipelineStage, null=True, on_delete=models.SET_NULL)

    class Meta:
        app_label = "tests"


@strawberry_django.type(Task)
class CreateTaskType(AngeeNode):
    title: auto
    status: auto


@strawberry_django.type(Stage)
class CreateStageType(AngeeNode):
    name: auto
    category: auto


@pytest.fixture
def productivity_tables(transactional_db: None) -> Iterator[None]:
    """Create native stage rows for behavior regressions."""
    test_models = (RoutingStageContainer, RoutingPipelineStage, RoutingStageRecord)
    with model_tables(test_models):
        yield


@pytest.fixture
def productivity_create_case(composed_permissions: None) -> Iterator[tuple[Any, Any, Queue]]:
    """Expose the actual production Task and Stage resources under native permissions."""

    del composed_permissions
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
                Task,
                "create_tasks",
                ["title", "project", "queue", "stage"],
                ("project", "queue", "stage"),
            ),
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
                            CreateStageType,
                            *(item for resource in resources for item in resource.types),
                        ],
                    }
                }
            )
        ]
    ).build("public")
    yield (schema, admin, queue)


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
    task = Task.objects.as_user(actor).get(sqid=created["id"])
    assert task.queue_id == queue.pk
    assert task.stage_id == stage.pk
    assert task.status == Task.TaskStatus.DONE
    assert task.done_at is not None
    assert task.number == 1


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
def test_bulk_snooze_wake_clears_snooze_fields(composed_permissions: None) -> None:
    """Use the real Work task queryset instead of invoking a donor on a foreign model."""

    del composed_permissions
    now = timezone.now()
    with system_context(reason="tests.productivity.wake"):
        owner = create_platform_admin("productivity-wake-owner")
        row = Task(title="Snoozed", snoozed_until=now, snoozed_by=owner)
        row.sudo(reason="tests.productivity.wake.create").save(ownerless=True)
        assert Task.objects.filter(pk=row.pk).wake_due_snoozes(now=now) == 1
    row.refresh_from_db()
    assert row.snoozed_until is None
    assert row.snoozed_by is None
