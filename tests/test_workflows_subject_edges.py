"""Canonical subjects stay discoverable across trigger and child-run boundaries."""

from datetime import timedelta

import pytest
import strawberry_django
from django.db.models.functions import Now
from rebac import actor_context, system_context

from angee.base.refs import canonical_record_target
from angee.base.scoping import system_queryset
from angee.graphql.data import hasura_model_resource
from angee.graphql.node import AngeeNode
from angee.graphql.schema import GraphQLSchemas
from angee.workflows.runner import runner
from angee.workflows.testing.drivers import load_workflow, run_until, start_run, trigger_source
from angee.workflows.testing.models import StepRun, Trigger, TriggerEvent, WorkflowRun
from angee.workflows.triggers import RecordChanged
from tests.conftest import make_addon
from tests.mtidemo.models import MtiChild, MtiParent
from tests.test_workflows_children import child_graph as child_graph
from tests.workflow_steps import document

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("workflow_step_classes")]


@pytest.fixture
def child_source(monkeypatch):
    """Expose the real child through a temporary native resource declaration."""
    @strawberry_django.type(MtiChild)
    class SubjectChild(AngeeNode):
        detail: str

    resource = hasura_model_resource(
        SubjectChild, model=MtiChild, filterable=("id", "detail"), sortable=("id",), aggregatable=(),
        insert=False, update=False, delete=False,
    )
    owner = GraphQLSchemas([make_addon(schemas={"console": {
        "query": [resource.query], "types": resource.types,
    }})])
    monkeypatch.setattr(GraphQLSchemas, "_discovered", owner)
    with trigger_source(MtiChild):
        yield


def test_trigger_and_manual_mti_subjects_share_identity_through_retention(execution, child_source, settings):
    """Trigger admission uses the same searchable reference as manual admission."""
    actor, _sent = execution
    with system_context(reason="tests workflow trigger child subject"):
        subject = MtiChild.objects.create(title="Canonical subject", detail="Ready")
    target = canonical_record_target(subject)
    assert target.content_type.model_class() is MtiParent
    workflow = load_workflow(
        document("entry"), actor=actor, key="child-subject-trigger", subject_model=MtiChild._meta.label_lower,
    )
    manual = start_run(workflow, actor=actor, subject=subject)
    with actor_context(actor):
        trigger = Trigger.objects.create(
            workflow=workflow, source="record_changed", model_label=MtiChild._meta.label_lower,
            condition={"detail": {"_eq": "Ready"}},
        )
    Trigger.objects.enable(trigger, actor=actor)
    RecordChanged.dispatch(MtiChild, subject)
    assert Trigger.objects.drain() == 1
    event = system_queryset(TriggerEvent).get(trigger=trigger)
    triggered = event.started_run
    for run in (manual, triggered):
        assert (run.subject_content_type_id, run.subject_object_id) == (target.content_type.pk, target.object_id)
    for record in (subject, system_queryset(MtiParent).get(pk=subject.pk)):
        assert set(WorkflowRun.objects.with_actor(actor).for_subject(record).values_list("pk", flat=True)) == {
            manual.pk, triggered.pk,
        }
    run_until(triggered)
    assert triggered.status == "succeeded"
    settings.ANGEE_WORKFLOW_RETENTION_DAYS = 1
    system_queryset(WorkflowRun).filter(pk=triggered.pk).update(finished_at=Now() - timedelta(days=2))
    assert WorkflowRun.objects.prune() == 1
    event.refresh_from_db()
    assert event.admitted_at and not system_queryset(WorkflowRun).filter(trigger_event=event).exists()
    RecordChanged.dispatch(MtiChild, subject)
    assert Trigger.objects.drain() == 0
    assert list(WorkflowRun.objects.with_actor(actor).for_subject(subject).values_list("pk", flat=True)) == [manual.pk]


def test_awaited_child_run_keeps_its_canonical_subject(child_graph):
    """A real parent starts, awaits and finishes a child on the same subject reference."""
    actor, _sent, admitted, build = child_graph
    with system_context(reason="tests workflow awaited child subject"):
        subject = MtiChild.objects.create(title="Awaited subject", detail="Child detail")
    parent, _workflow = build(subject=subject, subject_model=MtiChild._meta.label_lower)
    run_until(parent)
    child = admitted[0]
    target = canonical_record_target(subject)
    assert (child.subject_content_type_id, child.subject_object_id) == (target.content_type.pk, target.object_id)
    waiter = system_queryset(StepRun).get(run=parent, node_key="await")
    assert waiter.waiting_kind == "run" and waiter.awaited_run_id == child.pk
    assert WorkflowRun.objects.with_actor(actor).for_subject(subject).get().pk == child.pk
    run_until(child)
    assert runner.wake_runs(child.pk) == 1
    run_until(parent)
    assert child.status == parent.status == "succeeded"
    assert WorkflowRun.objects.with_actor(actor).for_subject(subject).get().pk == child.pk
