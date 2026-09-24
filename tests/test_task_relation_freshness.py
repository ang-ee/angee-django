"""Task invariants use persisted facts when related objects are cached or edited."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, models
from rebac import system_context

from angee.projects.models import Milestone as AbstractMilestone
from angee.projects.models import Task as AbstractTask
from angee.work.models import Stage as AbstractStage
from angee.work.models import TaskWork
from tests.conftest import _clear_model_tables, _create_missing_tables
from tests.scopedemo.models import Scope


def _local_constraints(constraints: tuple[models.BaseConstraint, ...]) -> tuple[models.BaseConstraint, ...]:
    """Copy inherited constraints under module-local names; SQLite index names are global."""

    copies = []
    for constraint in constraints:
        path, args, kwargs = constraint.deconstruct()
        kwargs["name"] = f"freshness_{kwargs['name']}"
        copies.append(type(constraint)(*args, **kwargs))
    return tuple(copies)



class FreshnessStage(AbstractStage):
    queue = models.ForeignKey(Scope, on_delete=models.CASCADE)

    class Meta(AbstractStage.Meta):
        abstract = False
        app_label = "scopedemo"
        constraints = _local_constraints(AbstractStage.Meta.constraints)


class FreshnessMilestone(AbstractMilestone):
    project = models.ForeignKey(Scope, on_delete=models.CASCADE)

    class Meta(AbstractMilestone.Meta):
        abstract = False
        app_label = "scopedemo"
        constraints = _local_constraints(getattr(AbstractMilestone.Meta, "constraints", ()))


class FreshnessTask(TaskWork, AbstractTask):
    project = models.ForeignKey(Scope, null=True, blank=True, on_delete=models.SET_NULL)
    milestone = models.ForeignKey(FreshnessMilestone, null=True, blank=True, on_delete=models.SET_NULL)
    queue = models.ForeignKey(Scope, null=True, blank=True, on_delete=models.SET_NULL)
    stage = models.ForeignKey(FreshnessStage, null=True, blank=True, on_delete=models.SET_NULL)
    cycle = None
    cycle_id = None
    converted_from_activity = None
    links = None
    thread_attachments = None
    thread_create_log = False
    thread_create_autofollow_author = False
    thread_tracking_fields = ()

    class Meta(AbstractTask.Meta):
        abstract = False
        app_label = "scopedemo"
        constraints = _local_constraints(AbstractTask.Meta.constraints[:2])


@pytest.fixture
def task_relations(transactional_db: None) -> Iterator[Scope]:
    del transactional_db
    test_models = (FreshnessStage, FreshnessMilestone, FreshnessTask, FreshnessTask.history.model)
    created = _create_missing_tables(test_models)
    try:
        with system_context(reason="tests.task_relation_freshness.setup"):
            scope = Scope.objects.create(name="Task scope")
        yield scope
    finally:
        _clear_model_tables(test_models)
        if created:
            with connection.schema_editor() as editor:
                for model in reversed(created):
                    editor.delete_model(model)


def test_unsaved_stage_category_edit_cannot_allow_system_stage_entry(task_relations: Scope) -> None:
    with system_context(reason="tests.task_relation_freshness.system_stage"):
        stage = FreshnessStage.objects.create(queue=task_relations, name="Triage", category="triage")
    task = FreshnessTask(title="Must use capture", queue=task_relations, number=1)
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
        )
        changed_stage = FreshnessStage._base_manager.get(pk=stage.pk)
        changed_stage.category = "completed"
        changed_stage.save(update_fields=("category",))
        assert task.stage.category == "started"

        task.title = "Now complete"
        task.save(update_fields=("title",))

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
