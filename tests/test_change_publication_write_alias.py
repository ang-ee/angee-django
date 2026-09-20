"""A source change and its workflow admission share the source commit alias."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from django.db import connection, connections, router, transaction
from rebac import system_context

from angee.graphql import publishing
from tests.test_transitions import TransitionRouter
from tests.test_workflows_triggers import (
    TriggerSubject,
    _event_trigger,
    executable_fixture,  # noqa: F401
    workflow_trigger_tables,  # noqa: F401
)
from tests.workflows import StepRun, Trigger, WorkflowDispatch, WorkflowRun


@pytest.fixture
def change_writer(workflow_trigger_tables: None) -> Iterator[str]:  # noqa: F811 - imported pytest fixture
    """Reuse the native secondary-connection pattern from transition tests."""

    del workflow_trigger_tables
    alias = "change_publication_writer"
    connections[alias] = connection.copy(alias=alias)
    try:
        yield alias
    finally:
        connections[alias].close()
        del connections[alias]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("publication", ["signal", "persisted", "explicit"])
@pytest.mark.parametrize("rollback", [False, True])
def test_source_change_admits_only_after_its_selected_writer_commits(
    change_writer: str,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
    publication: str,
    rollback: bool,
) -> None:
    """Commit delivers the selected alias through the real trigger admission owner."""

    del no_workflow_queue
    subject = TriggerSubject.objects.using(change_writer).create(name="source", state="ready")
    trigger = _event_trigger(condition={"state": "ready"})
    routing = TransitionRouter(change_writer)
    monkeypatch.setattr(router, "routers", [routing])
    admissions: list[tuple[str | None, str | None, str | None]] = []
    broadcasts: list[dict[str, Any]] = []
    manager_class = type(Trigger.objects)
    start_event = manager_class.start_event

    def admit(manager: Any, *args: Any, **kwargs: Any) -> Any:
        run = start_event(manager, *args, **kwargs)
        assert run is not None
        admissions.append((manager._db, kwargs["subject"]._state.db, run._state.db))
        return run

    monkeypatch.setattr(manager_class, "start_event", admit)
    monkeypatch.setattr(
        publishing, "_broadcast", lambda model, payload: broadcasts.append(payload) if model is TriggerSubject else None
    )
    publishing.connect_change_broadcast_receiver()

    with transaction.atomic(using=change_writer):
        if publication == "signal":
            subject.name = "updated"
            subject.save(using=change_writer, update_fields={"name"})
        else:
            if publication == "explicit":
                subject._state.db = "unselected_instance_alias"
            publishing.publish_change(
                subject,
                action="update",
                update_fields={"name"},
                using=change_writer if publication == "explicit" else None,
            )
        assert admissions == []
        assert broadcasts == []
        with system_context(reason="test change publication before commit"):
            assert not WorkflowRun.objects.using(change_writer).filter(trigger_id=trigger.pk).exists()
        if rollback:
            transaction.set_rollback(True, using=change_writer)

    with system_context(reason="test change publication after commit"):
        runs = WorkflowRun.objects.using(change_writer).filter(trigger_id=trigger.pk)
        if rollback:
            assert not runs.exists()
            assert admissions == []
            assert broadcasts == []
            return
        run = runs.get()
        assert run.subject_object_id == subject.pk
        assert StepRun.objects.using(change_writer).filter(run_id=run.pk).count() == 1
        assert WorkflowDispatch.objects.using(change_writer).filter(run_id=run.pk).exists()
    assert admissions == [(change_writer, change_writer, change_writer)]
    # Django may route newly constructed FK owners while assigning their
    # related objects; the publication receiver must never route the sender.
    assert None not in routing.writes
    assert len(broadcasts) == 1
    assert "using" not in broadcasts[0]
