"""Productivity save owners preserve Django's deferred-column update boundary."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.signals import pre_save
from django.utils import timezone
from rebac import system_context

import tests.scopedemo.models  # noqa: F401 -- register related models before native database setup
import tests.spaces_models  # noqa: F401 -- register related models before native database setup
import tests.test_sequence  # noqa: F401 -- register related models before native database setup
from angee.portfolio.models import Initiative as AbstractInitiative
from angee.portfolio.models import InitiativeProject as AbstractInitiativeProject
from angee.portfolio.models import Update as AbstractUpdate
from angee.projects.models import TaskRelation as AbstractTaskRelation
from angee.work.models import Cycle as AbstractCycle
from tests.conftest import Backend, Drive
from tests.projects_models import Project, ProjectBinding
from tests.test_productivity_write_behavior import Queue, Stage
from tests.test_project_access import project_access_schema as project_access_schema
from tests.test_task_relation_freshness import FreshnessTask


class Cycle(AbstractCycle):
    class Meta(AbstractCycle.Meta):
        abstract = False
        app_label = "work"
        rebac_resource_type = "work/cycle"


class Initiative(AbstractInitiative):
    class Meta(AbstractInitiative.Meta):
        abstract = False
        app_label = "portfolio"
        rebac_resource_type = "portfolio/initiative"


class InitiativeProject(AbstractInitiativeProject):
    class Meta(AbstractInitiativeProject.Meta):
        abstract = False
        app_label = "portfolio"
        rebac_resource_type = "portfolio/initiative_project"


class Update(AbstractUpdate):
    class Meta(AbstractUpdate.Meta):
        abstract = False
        app_label = "portfolio"
        rebac_resource_type = "portfolio/update"


class TaskRelation(AbstractTaskRelation):
    task = models.ForeignKey(FreshnessTask, on_delete=models.CASCADE, related_name="+")
    related_task = models.ForeignKey(FreshnessTask, on_delete=models.CASCADE, related_name="+")

    class Meta(AbstractTaskRelation.Meta):
        abstract = False
        app_label = "projects"
        rebac_resource_type = "projects/task_relation"


@pytest.fixture
def deferred_save_rows(project_access_schema: Any) -> Iterator[dict[str, models.Model]]:
    del project_access_schema
    with system_context(reason="tests.productivity.deferred_save.setup"):
        queue = Queue.objects.create(key="DEFERRED", name="Deferred", slug="deferred")
        cycle = Cycle.objects.create(queue=queue, number=1, starts_on=date(2026, 1, 1), ends_on=date(2026, 1, 7))
        task = FreshnessTask.objects.create(title="Original")
        related = FreshnessTask.objects.create(title="Related")
        relation = TaskRelation.objects.create(task=task, related_task=related, kind="blocks")
        project = Project.objects.create(title="Original")
        backend = Backend.objects.create(slug="deferred", label="Deferred", backend_class="local")
        drive = Drive.objects.create(backend=backend, slug="deferred", name="Deferred")
        binding = ProjectBinding.objects.create(project=project, target=drive)
        initiative = Initiative.objects.create(name="Original")
        placement = InitiativeProject.objects.create(initiative=initiative, project=project)
        report = Update.objects.create(target=initiative, health="on_track", body="Original")
        rows = {
            "queue": queue,
            "stage": Stage.objects.get(queue=queue, category="unstarted"),
            "cycle": cycle,
            "task": task,
            "relation": relation,
            "project": project,
            "binding": binding,
            "initiative": initiative,
            "placement": placement,
            "report": report,
        }
    yield rows


@pytest.mark.parametrize(
    ("owner", "edited_field", "edited_value"),
    [
        ("queue", "key", "UPDATED"),
        ("stage", "name", "Updated"),
        ("cycle", "name", "Updated"),
        ("task", "title", "Updated"),
        ("relation", "kind", "relates"),
        ("project", "title", "Updated"),
        ("binding", "updated_at", None),
        ("initiative", "name", "Updated"),
        ("placement", "sort_order", 2048.0),
        ("report", "body", "Updated"),
    ],
)
def test_save_preserves_concurrent_change_to_deferred_column(
    deferred_save_rows: dict[str, models.Model], owner: str, edited_field: str, edited_value: Any
) -> None:
    """A save without update_fields cannot overwrite a column the caller deferred."""
    row = deferred_save_rows[owner]
    model = type(row)
    loaded = model._base_manager.defer("created_at").get(pk=row.pk)
    assert "created_at" not in loaded.__dict__
    concurrent_created_at = timezone.now() - timedelta(days=1)
    updates: list[int] = []

    def update_deferred_column(sender: type[models.Model], instance: models.Model, **kwargs: Any) -> None:
        if instance is loaded:
            updates.append(sender._base_manager.filter(pk=instance.pk).update(created_at=concurrent_created_at))

    if edited_value is not None:
        setattr(loaded, edited_field, edited_value)
    pre_save.connect(update_deferred_column, sender=model)
    try:
        with system_context(reason="tests.productivity.deferred_save.persist"):
            loaded.save()
    finally:
        pre_save.disconnect(update_deferred_column, sender=model)

    persisted = model._base_manager.get(pk=row.pk)
    assert updates == [1]
    assert persisted.created_at == concurrent_created_at
    if edited_value is not None:
        assert getattr(persisted, edited_field) == edited_value


def test_system_stage_deferred_identity_allows_unrelated_edit(deferred_save_rows: dict[str, models.Model]) -> None:
    queue = deferred_save_rows["queue"]
    stage = Stage._base_manager.only("id", "tone").get(queue=queue, category="triage")
    stage.tone = "info"
    stage.sudo(reason="tests.productivity.stage.persist")
    # Instance-level authorization bypass leaves the Stage identity guard active.
    stage.save()
    assert Stage._base_manager.get(pk=stage.pk).tone == "info"


def test_system_stage_deferred_identity_still_rejects_rename(deferred_save_rows: dict[str, models.Model]) -> None:
    queue = deferred_save_rows["queue"]
    stage = Stage._base_manager.only("id", "tone").get(queue=queue, category="triage")
    stage.name = "Renamed"
    stage.sudo(reason="tests.productivity.stage.rename")
    with pytest.raises(ValidationError, match="cannot be renamed"):
        stage.save()
    assert Stage._base_manager.get(pk=stage.pk).name == "Triage"
