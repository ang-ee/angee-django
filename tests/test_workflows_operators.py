"""Workflow-wide operators compose the existing run and sharing policy owners."""

import pytest
from django.core.exceptions import PermissionDenied
from django.db import transaction
from rebac import to_subject_ref

from angee.base.identity import public_subject_ref
from angee.base.scoping import system_queryset
from angee.graphql import records
from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.workflows import schema as workflow_schema
from angee.workflows import tasks
from angee.workflows.testing.drivers import load_workflow, run_until, start_run
from angee.workflows.testing.models import StepRun, Workflow, WorkflowRun
from tests.conftest import SchemaAddon, create_user, execute_schema, result_data
from tests.test_workflows_cancel import cancellations
from tests.workflow_steps import Echo, document

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("workflow_step_classes")]


@pytest.fixture
def operator_run(execution):
    """A non-admin author delegates operation separately from starting and editing."""
    admin, _sent = execution
    author, starter, operator, other = (
        create_user(f"workflow-{role}") for role in ("author", "starter", "operator", "other-starter")
    )
    workflow = load_workflow(document("entry", step="reject"), key="operator-target", actor=admin)
    workflow.with_actor(admin).grant_record_access("editor", author)
    for actor in (starter, other):
        workflow.with_actor(author).grant_record_access("starter", actor)
    workflow.with_actor(author).grant_record_access("operator", operator)
    run = start_run(workflow, actor=starter)
    return workflow, run, author, starter, operator, other


@pytest.fixture
def operator_schema():
    """Compose the resource metadata and IAM's existing record-access API."""
    parts = {
        key: [*workflow_schema.schemas["console"].get(key, ()), *records.schemas["console"].get(key, ())]
        for key in SCHEMA_PART_KEYS
    }
    return GraphQLSchemas([SchemaAddon({"console": parts})]).build("console")


def test_workflow_operator_reads_only_its_workflow_and_operates_without_editing(operator_run, execution):
    workflow, run, _author, _starter, operator, other = operator_run
    unrelated = load_workflow(document("entry"), key="unrelated-operator-workflow", actor=execution[0])
    unrelated_run = start_run(unrelated, actor=execution[0])
    assert not operator.is_superuser
    assert workflow.with_actor(operator).has_access("read")
    assert workflow.with_actor(operator).has_access("start")
    assert not workflow.with_actor(operator).has_access("write")
    assert not workflow.with_actor(operator).has_access("monitor")
    assert set(Workflow.objects.with_actor(operator).values_list("pk", flat=True)) == {workflow.pk}
    assert set(WorkflowRun.objects.with_actor(operator).values_list("pk", flat=True)) == {run.pk}
    assert not unrelated_run.can_cancel(operator)
    assert not run.can_cancel(other)
    assert run.can_cancel(operator)
    with pytest.raises(PermissionDenied):
        workflow.with_actor(operator).grant_record_access("operator", other)
    with pytest.raises(PermissionDenied):
        WorkflowRun.objects.cancel(run, actor=other)
    assert WorkflowRun.objects.cancel(run, actor=operator).canceled
    run.refresh_from_db()
    assert run.status == "canceled"


def test_workflow_operator_retries_and_reprocesses_another_users_run(operator_run):
    _workflow, run, _author, starter, operator, other = operator_run
    run_until(run)
    step = system_queryset(StepRun).get(run=run)
    assert run.status == "failed" and step.can_retry(operator)
    assert not step.can_retry(other)
    with pytest.raises(PermissionDenied):
        StepRun.objects.retry_step(step, actor=other)
    retried = StepRun.objects.retry_step(step, actor=operator)
    assert retried.status == "ready"
    run_until(run)
    assert run.can_reprocess(operator) and not run.can_reprocess(other)
    with pytest.raises(PermissionDenied):
        WorkflowRun.objects.reprocess(run, actor=other)
    replacement = WorkflowRun.objects.reprocess(run, actor=operator)
    assert replacement.run_as_id == operator.pk != starter.pk
    assert replacement.reprocess_of_id == run.pk


@pytest.mark.parametrize("revoke_before_delivery", [False, True])
def test_workflow_operator_deferred_cancel_rechecks_the_workflow_grant(
    operator_run, execution, revoke_before_delivery,
):
    workflow, run, author, _starter, operator, other = operator_run
    _admin, sent = execution
    with pytest.raises(PermissionDenied):
        WorkflowRun.objects.cancel_on_commit(run, other)
    with transaction.atomic():
        WorkflowRun.objects.cancel_on_commit(run, operator)
        assert cancellations(sent) == []
    assert len(cancellations(sent)) == 1
    if revoke_before_delivery:
        workflow.with_actor(author).revoke_record_access("operator", operator)
        with pytest.raises(PermissionDenied):
            tasks.cancel(**cancellations(sent)[0])
    else:
        tasks.cancel(**cancellations(sent)[0])
    run.refresh_from_db()
    assert run.status == ("running" if revoke_before_delivery else "canceled")


@pytest.mark.parametrize("authorized", [False, True])
def test_context_cancellation_uses_workflow_operator_authority(operator_run, execution, register_step, authorized):
    _workflow, target, _author, starter, operator, other = operator_run
    admin, sent = execution
    actor = operator if authorized else other
    target.with_actor(starter).grant_record_access("reader", other)

    class CancelTarget(Echo):
        key = "operator_cancel_target"

        def run(self, ctx):
            ctx.cancel_run(ctx.load(WorkflowRun, target.sqid))
            return ctx.done(ctx.input)

    register_step(CancelTarget)
    caller_workflow = load_workflow(document("entry", step=CancelTarget.key), key="operator-caller", actor=admin)
    caller_workflow.with_actor(admin).grant_record_access("starter", actor)
    caller = start_run(caller_workflow, actor=actor)
    run_until(caller)
    assert caller.status == ("succeeded" if authorized else "failed")
    assert len(cancellations(sent)) == int(authorized)
    if authorized:
        tasks.cancel(**cancellations(sent)[0])
    target.refresh_from_db()
    assert target.status == ("canceled" if authorized else "running")


def test_operator_grants_surface_beside_starters_through_shared_access_owner(operator_run, operator_schema):
    workflow, _run, author, _starter, operator, other = operator_run
    resource = next(item for item in operator_schema.angee_resources if item.model_label == "workflows.Workflow")
    relations = {item.relation: item for item in resource.grantable}
    assert relations["operator"].permission == relations["starter"].permission == "write"
    assert {(item.type, item.relation) for item in relations["operator"].subjects} == {
        ("auth/user", None), ("auth/group", "member"),
    }
    query = """query($id: ID!) {
      record_access_options(target_type: "workflows/workflow", target_ids: [$id]) { relation permission }
      record_access(target_type: "workflows/workflow", target_ids: [$id]) { relation subject }
    }"""
    data = result_data(execute_schema(operator_schema, query, {"id": workflow.sqid}, user=author))
    assert {"relation": "operator", "permission": "write"} in data["record_access_options"]
    assert any(item["relation"] == "operator" for item in data["record_access"])
    mutation = """mutation($id: ID!, $subject: String!) {
      grant_record_access(target_type: "workflows/workflow", target_ids: [$id],
        relation: "operator", subject: $subject) { ok }
    }"""
    subject = str(public_subject_ref(to_subject_ref(other)))
    variables = {"id": workflow.sqid, "subject": subject}
    denied = result_data(execute_schema(operator_schema, mutation, variables, user=operator))
    assert not denied["grant_record_access"]["ok"]
    granted = result_data(execute_schema(operator_schema, mutation, variables, user=author))
    assert granted["grant_record_access"]["ok"]
