"""A run projects one timeline line per record it worked on, never one on itself."""

from __future__ import annotations

import pytest

from angee.identity import public_id_of
from angee.refs import generic_pointer_target
from angee.timeline.events import sync_events
from angee.timeline.testing.models import Event
from angee.workflows.states import RunStatus
from angee.workflows.testing.drivers import load_workflow, run_until
from angee.workflows.testing.models import WorkflowRun
from angee.workflows.timeline import RunKind, run_event_key
from tests.conftest import create_user
from tests.workflow_steps import document

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("workflow_step_classes")]


def test_run_projects_one_line_per_record_and_follows_its_status(execution) -> None:
    actor, _sent = execution
    subject = create_user("timeline-run-subject")
    workflow = load_workflow(
        document("entry"), key="timeline-run", actor=actor, subject_model=actor._meta.label, name="Timeline run",
    )
    run = WorkflowRun.objects.start(workflow, actor=actor, subject=subject)
    kind = RunKind()
    sync_events(run, kinds=(kind,))
    event_model = Event
    events = list(event_model.objects.filter(kind=kind.id))
    content_type, object_id = generic_pointer_target(subject)
    label = content_type.model_class()._meta.label
    assert [event.key for event in events] == [f"workflows.run:{public_id_of(run)}:{label}/{object_id}"]
    assert events[0].key == run_event_key(run, subject)
    assert (events[0].record_content_type_id, events[0].record_object_id) == (content_type.pk, object_id)
    assert (events[0].target_model, events[0].target_id) == ("workflows.WorkflowRun", public_id_of(run))
    assert events[0].title == "Timeline run"
    assert events[0].actor_label == actor.get_username()
    assert events[0].detail == "Running"
    assert events[0].parent_id is None
    # The run never lists itself: its own page shows only child runs and its decisions.
    assert not event_model.objects.filter(record_object_id=run.pk, target_id=public_id_of(run)).exists()

    run_until(run)
    run.refresh_from_db()
    assert run.status == RunStatus.SUCCEEDED
    # Syncing again rewrites the same key with the new status and nothing else.
    assert sync_events(run, kinds=(kind,)) == 1
    assert sync_events(run, kinds=(kind,)) == 0
    event = event_model.objects.get(key=run_event_key(run, subject))
    assert event.detail == "Succeeded"
    assert not event.needs.sudo(reason="test").exists()
