"""Task invariants use persisted facts when related objects are cached or edited."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, models
from rebac import system_context

from angee.projects.testing.models import Milestone, Stage, Task
from tests.scopedemo.models import FreshnessMilestone, FreshnessStage, FreshnessTask, Scope


@pytest.mark.parametrize(
    ("model", "extension"),
    ((Task, FreshnessTask), (Stage, FreshnessStage), (Milestone, FreshnessMilestone)),
)
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
                    columns = []
                    for name in constraint.fields:
                        field = concrete._meta.get_field(name)
                        assert isinstance(field, models.Field)
                        columns.append(field.column)
                    assert stored[constraint.name]["columns"] == columns


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
    stage.category = FreshnessStage.StageCategory.STARTED
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
        changed_stage.category = FreshnessStage.StageCategory.COMPLETED
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
