"""Productivity writers retain their alias or reject unsupported REBAC work."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import connection, models, router
from django.utils import timezone

from angee.base.stages import Stage, StagedModelMixin
from angee.portfolio.models import ProductManager, UpdateManager
from angee.projects.models import LinkManager, ProjectManager, TaskManager
from angee.proposals.models import ProposalManager
from angee.work.models import QueueManager, TaskWork
from tests.projects_models import Task
from tests.proposals_models import Proposal, Round
from tests.test_transitions import TransitionRouter


class RoutingStageContainer(models.Model):
    """Small native container for the shared stage owner's routing contract."""

    default_stage = models.ForeignKey("tests.RoutingPipelineStage", null=True, on_delete=models.SET_NULL)

    class Meta:
        app_label = "tests"


class RoutingPipelineStage(Stage):
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
        (TaskManager, "check_create", (), {}),
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
