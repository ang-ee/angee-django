"""Task invariants use persisted facts when related objects are cached or edited."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, models
from rebac import system_context

from angee.projects.models import Milestone as AbstractMilestone
from angee.projects.models import Task as AbstractTask
from angee.projects.testing.models import Stage, Task
from angee.work.models import Stage as AbstractStage
from angee.work.models import TaskWork
from tests.conftest import standalone_relation
from tests.scopedemo.models import Scope


class FreshnessStage(AbstractStage):
    queue = models.ForeignKey(Scope, on_delete=models.CASCADE)

    class Meta(AbstractStage.Meta):
        abstract = False
        app_label = "scopedemo"
        # Its own type: the production type has one concrete model, in angee.projects.testing.models.
        rebac_resource_type = "scopedemo/freshness_stage"


class FreshnessMilestone(AbstractMilestone):
    project = models.ForeignKey(Scope, on_delete=models.CASCADE)

    class Meta(AbstractMilestone.Meta):
        abstract = False
        app_label = "scopedemo"
        # Its own type: the production type has one concrete model, in angee.projects.testing.models.
        rebac_resource_type = "scopedemo/freshness_milestone"


class FreshnessTask(TaskWork, AbstractTask):
    # This scope intentionally supplies no item-ownership policy.
    owner_container = None
    assignee = standalone_relation(AbstractTask, "assignee")
    delegate = standalone_relation(AbstractTask, "delegate")
    activity_type = standalone_relation(AbstractTask, "activity_type")
    project = models.ForeignKey(Scope, null=True, blank=True, on_delete=models.SET_NULL)
    milestone = models.ForeignKey(FreshnessMilestone, null=True, blank=True, on_delete=models.SET_NULL)
    queue = models.ForeignKey(Scope, null=True, blank=True, on_delete=models.SET_NULL)
    stage = models.ForeignKey(FreshnessStage, null=True, blank=True, on_delete=models.SET_NULL)
    cycle = None
    cycle_id = None
    links = None
    file_attachments = None
    thread_attachments = None
    thread_create_autofollow_author = False

    class Meta(AbstractTask.Meta):
        abstract = False
        app_label = "scopedemo"
        # Its own type: the production type has one concrete model, in angee.projects.testing.models.
        rebac_resource_type = "scopedemo/freshness_task"


@pytest.mark.parametrize(("model", "extension"), ((Task, FreshnessTask), (Stage, FreshnessStage)))
def test_inherited_constraints_are_local_to_concrete_tables(
    db: None, model: type[models.Model], extension: type[models.Model],
) -> None:
    """Multiple concrete declarations retain their own checks and partial unique indexes."""

    del db
    assert {constraint.name for constraint in model._meta.constraints}.isdisjoint(
        constraint.name for constraint in extension._meta.constraints
    )
    with connection.cursor() as cursor:
        for concrete in (model, extension):
            stored = connection.introspection.get_constraints(cursor, concrete._meta.db_table)
            for constraint in concrete._meta.constraints:
                if isinstance(constraint, models.CheckConstraint):
                    assert stored[constraint.name]["check"]
                elif isinstance(constraint, models.UniqueConstraint) and constraint.condition is not None:
                    assert stored[constraint.name]["unique"]
                    assert stored[constraint.name]["columns"] == [
                        concrete._meta.get_field(name).column for name in constraint.fields
                    ]


@pytest.fixture
def task_relations(transactional_db: None) -> Iterator[Scope]:
    del transactional_db
    with system_context(reason="tests.task_relation_freshness.setup"):
        scope = Scope.objects.create(name="Task scope")
    yield scope


def test_unsaved_stage_category_edit_cannot_allow_system_stage_entry(task_relations: Scope) -> None:
    with system_context(reason="tests.task_relation_freshness.system_stage"):
        stage = FreshnessStage.objects.create(queue=task_relations, name="Triage", category="triage")
    task = FreshnessTask(title="Must use triage verb", queue=task_relations, number=1, estimate=1)
    stage.category = "started"
    task.stage = stage

    with pytest.raises(ValidationError, match="Use capture to move a task into the system triage stage"):
        task.save()

    assert not FreshnessTask._base_manager.exists()
    assert FreshnessStage._base_manager.get(pk=stage.pk).category == "triage"


def test_cached_stage_recategorization_projects_current_lifecycle(task_relations: Scope) -> None:
    with system_context(reason="tests.task_relation_freshness.lifecycle"):
        stage = FreshnessStage.objects.create(queue=task_relations, name="Active", category="started")
        task = FreshnessTask.objects.create(
            title="In progress",
            queue=task_relations,
            stage=stage,
            number=1,
            estimate=1,
        )
        changed_stage = FreshnessStage._base_manager.get(pk=stage.pk)
        changed_stage.category = "completed"
        changed_stage.save(update_fields=("category",))
        assert task.stage.category == "started"

        task.title = "Now complete"
        task.save(update_fields=("title",))
        persisted = FreshnessTask._base_manager.get(pk=task.pk)
        assert persisted.status == task.TaskStatus.OPEN
        assert persisted.done_at is None
        # Lifecycle belongs to a stage write; its projection must still ignore
        # the cached Stage object's old category.
        assert task.stage.category == "started"
        task.save(update_fields=("stage",))

    task.refresh_from_db()
    assert task.title == "Now complete"
    assert task.status == task.TaskStatus.DONE
    assert task.done_at is not None
    assert task.dropped_reason is None
    assert task.dropped_at is None


def test_cached_milestone_reassignment_rejects_old_project(task_relations: Scope) -> None:
    with system_context(reason="tests.task_relation_freshness.milestone"):
        new_project = Scope.objects.create(name="New project")
        milestone = FreshnessMilestone.objects.create(project=task_relations, name="Delivery")
        changed_milestone = FreshnessMilestone._base_manager.get(pk=milestone.pk)
        changed_milestone.project = new_project
        changed_milestone.save(update_fields=("project",))
        task = FreshnessTask(title="Old project", project=task_relations, milestone=milestone)
        assert task.milestone.project_id == task_relations.pk

        with pytest.raises(ValidationError, match="Milestone must belong to the task's project"):
            task.save()

    assert not FreshnessTask._base_manager.exists()
