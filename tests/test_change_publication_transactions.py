"""Change publication transactions."""

from __future__ import annotations

from typing import Any

import pytest
from django.db import transaction
from rebac import system_context

from angee.graphql import publishing
from angee.testing.models import StepRun, Trigger, WorkflowDispatch, WorkflowRun
from tests.test_workflows_triggers import TriggerSubject, _event_trigger
from tests.test_workflows_triggers import executable_fixture as executable_fixture
from tests.test_workflows_triggers import workflow_trigger_tables as workflow_trigger_tables


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("publication", ["signal", "explicit"])
@pytest.mark.parametrize("rollback", [False, True])
def test_source_change_admits_only_after_commit(
    workflow_trigger_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
    publication: str,
    rollback: bool,
) -> None:
    """Source change admits only after commit."""
    del no_workflow_queue
    subject = TriggerSubject.objects.create(name="source", state="ready")
    trigger = _event_trigger(condition={"state": "ready"})
    subject = TriggerSubject.objects.get(pk=subject.pk)
    admissions: list[int] = []
    broadcasts: list[dict[str, Any]] = []
    manager_class = type(Trigger.objects)
    start_event = manager_class.start_event

    def admit(manager: Any, *args: Any, **kwargs: Any) -> Any:
        run = start_event(manager, *args, **kwargs)
        assert run is not None
        admissions.append(run.pk)
        return run

    monkeypatch.setattr(manager_class, "start_event", admit)
    monkeypatch.setattr(
        publishing, "_broadcast", lambda model, payload: broadcasts.append(payload) if model is TriggerSubject else None
    )
    publishing.connect_change_broadcast_receiver()
    with transaction.atomic():
        if publication == "signal":
            subject.name = "updated"
            subject.save(update_fields={"name"})
        else:
            publishing.publish_change(subject, action="update", update_fields={"name"})
        assert admissions == []
        assert broadcasts == []
        with system_context(reason="test change publication before commit"):
            assert not WorkflowRun.objects.filter(trigger_id=trigger.pk).exists()
        if rollback:
            transaction.set_rollback(True)
    with system_context(reason="test change publication after commit"):
        runs = WorkflowRun.objects.filter(trigger_id=trigger.pk)
        if rollback:
            assert not runs.exists()
            assert admissions == []
            assert broadcasts == []
            return
        run = runs.get()
        assert run.subject_object_id == subject.pk
        assert StepRun.objects.filter(run_id=run.pk).count() == 1
        assert WorkflowDispatch.objects.filter(run_id=run.pk).exists()
    assert admissions == [run.pk]
    assert len(broadcasts) == 1
    assert "using" not in broadcasts[0]
