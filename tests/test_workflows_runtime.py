"""Database behavior of the rebuilt workflow managers."""

from contextlib import contextmanager
from datetime import timedelta

import psycopg.errors
import pytest
from celery.exceptions import SoftTimeLimitExceeded
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.management import call_command
from django.db import DataError, IntegrityError, OperationalError, transaction
from django.db.models.functions import Now
from pydantic import BaseModel, Field, field_serializer, field_validator
from rebac import actor_context, system_context
from rebac.roles import grant as grant_role

from angee.base.mixins import StaleRevisionError
from angee.base.scoping import system_queryset
from angee.workflows import runner as runner_module
from angee.workflows.definition import Definition, DefinitionInvalid
from angee.workflows.managers import StepAttemptQuerySet, StepRunQuerySet, WorkflowRunManager
from angee.workflows.runner import runner
from angee.workflows.states import RunStatus, StepRunStatus, WaitingKind
from angee.workflows.steps import Done, Fail, RetryPolicy, Step, StepMode, Superseded, Wait
from angee.workflows.testing.drivers import capture_tasks, load_workflow, observe, register_steps, run_until
from angee.workflows.testing.models import StepAttempt, StepRun, Workflow, WorkflowRun, WorkflowVersion
from tests.conftest import create_user
from tests.workflow_steps import Echo, document

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("workflow_step_classes")]


class RetainedBody(Echo):
    """Make a domain write that later engine failures must preserve."""

    def run(self, ctx):
        Workflow.objects.filter(pk=ctx.run.version.workflow_id).update(name="Successful body retained")
        return ctx.done(ctx.input)


def test_start_linear_run_and_immutable_version(execution):
    """A linear run commits both steps and never mutates its pinned document."""
    actor, sent = execution
    workflow = load_workflow(document("first", "second"), key="linear", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor, input={"value": 7})
    assert sent[0][0] == "workflows.execute"
    assert run.version_id == workflow.published_id
    run_until(run)
    run = system_queryset(WorkflowRun).get(pk=run.pk)
    assert (run.status, run.output, run.outcome) == (RunStatus.SUCCEEDED, {"value": 7}, "done")
    assert run.finished_at is not None
    assert system_queryset(StepAttempt).filter(step_run__run=run, result="succeeded").count() == 2
    version = system_queryset(WorkflowVersion).get(pk=run.version_id)
    original_document = version.document
    version.document = {}
    with pytest.raises(DjangoValidationError, match="cannot be edited"):
        version.save()
    reconstructed = WorkflowVersion(
        pk=version.pk,
        workflow_id=version.workflow_id,
        number=version.number,
        document={},
        content_hash=version.content_hash,
        created_at=version.created_at,
    )
    with (
        pytest.raises(IntegrityError, match="(?i)unique|duplicate"),
        system_context(reason="Test immutable snapshot collision"),
        transaction.atomic(),
    ):
        reconstructed.save()
    assert system_queryset(WorkflowVersion).get(pk=version.pk).document == original_document


def test_outcome_and_result_alias_store_the_shared_boundary(execution, register_step):
    """Step and run outcome columns retain their full 63-character declarations."""
    actor, _ = execution
    step_outcome, result_alias = "s" * 63, "r" * 63

    class BoundaryOutcome(Echo):
        """Complete with the largest declared success outcome."""

        key = "boundary_outcome"
        outcomes = {step_outcome: "Boundary"}

        def run(self, ctx):
            return ctx.done(ctx.input, outcome=step_outcome)

    register_step(BoundaryOutcome)
    workflow = load_workflow(
        {
            "nodes": {"entry": {"step": "boundary_outcome"}},
            "results": [{"from": "entry", "when": [step_outcome], "as": result_alias}],
        },
        key="outcome_boundary",
        actor=actor,
    )
    run = WorkflowRun.objects.start(workflow, actor=actor, input={"value": 23})
    run_until(run)
    step_run = system_queryset(StepRun).get(run=run)
    retained_run = system_queryset(WorkflowRun).get(pk=run.pk)
    assert (step_run.status, step_run.outcome) == (StepRunStatus.SUCCEEDED, step_outcome)
    assert (retained_run.status, retained_run.outcome) == (RunStatus.SUCCEEDED, result_alias)
    assert retained_run.output == {"value": 23} and retained_run.error == ""


@pytest.mark.parametrize("helper", ["direct", "context"])
@pytest.mark.parametrize(
    ("invalid", "cause"),
    [
        ("reserved", "error"), ("undeclared", "missing"), ("pattern", "outcome"),
        ("output", "output"), ("wait_state", "serialize"), ("fail_error", "error"),
    ],
)
def test_settlement_validation_rolls_back_body(execution, register_step, helper, invalid, cause):
    """Every settlement constructor defers validation to the transactional body boundary."""
    actor, _ = execution
    returned = []

    class InvalidSettlement(Echo):
        """Return unchecked values after making a transactional domain change."""

        key = "invalid_settlement"

        def run(self, ctx):
            Workflow.objects.filter(pk=ctx.run.version.workflow_id).update(name="Must roll back")
            done = ctx.done if helper == "context" else Done
            wait = ctx.wait if helper == "context" else Wait
            settlement = {
                "reserved": done(outcome="error"),
                "undeclared": done(outcome="missing"),
                "pattern": done(outcome="Invalid outcome"),
                "output": done(outcome="done", output={"value": "invalid"}),
                "wait_state": wait(until=ctx.now, state=object()),
                "fail_error": ctx.fail(object()) if helper == "context" else Fail(error=object()),
            }[invalid]
            returned.append(settlement)
            return settlement

    register_step(InvalidSettlement)
    workflow = load_workflow(document("entry", step=InvalidSettlement.key), key="unchecked", actor=actor)
    original_name = workflow.name
    run = WorkflowRun.objects.start(workflow, actor=actor)
    row = system_queryset(StepRun).get(run=run)

    assert runner.execute(row.pk)

    assert len(returned) == 1
    assert system_queryset(Workflow).get(pk=workflow.pk).name == original_name
    assert system_queryset(StepRun).get(pk=row.pk).status == StepRunStatus.FAILED
    attempt = system_queryset(StepAttempt).get(step_run=row)
    assert attempt.result == "failed" and cause in attempt.error
    assert system_queryset(WorkflowRun).get(pk=run.pk).error == ""


def test_claim_retains_run_identity_and_parses_definition_once(execution, register_step, monkeypatch):
    """The body and planner share the locked run and its single parsed document."""
    actor, _ = execution
    seen = []

    class InspectClaim(Echo):
        """Observe the actual context assembled after the claim."""

        key = "inspect_claim"

        def run(self, ctx):
            seen.append((ctx.run is ctx.step_run.run, ctx.step is ctx.step_run.step))
            return ctx.done(ctx.input)

    register_step(InspectClaim)
    workflow = load_workflow(document("entry", step=InspectClaim.key), key="cached_claim", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    row = system_queryset(StepRun).get(run=run)
    parse = Definition.model_validate
    parsed = []

    def count_parse(cls, *args, **kwargs):
        definition = parse(*args, **kwargs)
        parsed.append(definition)
        return definition

    monkeypatch.setattr(Definition, "model_validate", classmethod(count_parse))

    assert runner.execute(row.pk)
    assert seen == [(True, True)]
    assert len(parsed) == 1


@pytest.mark.parametrize("helper", ["context", "class", "direct"])
def test_success_output_is_validated_once_before_serialization(execution, register_step, helper):
    """Native validation aliases and serializers survive every completion constructor."""
    actor, _ = execution
    validated = []

    class AliasedOutput(BaseModel):
        """A validation contract intentionally distinct from its persisted representation."""

        value: int = Field(validation_alias="raw", serialization_alias="value")

        @field_validator("value")
        @classmethod
        def record_validation(cls, value):
            validated.append(value)
            return value

        @field_serializer("value")
        def serialize_value(self, value):
            return value + 1

    class AliasedStep(Step[None, AliasedOutput, None]):
        """Return the same raw output through each supported constructor."""

        key = "aliased_output"

        def run(self, ctx):
            if helper == "context":
                return ctx.done({"raw": 21})
            if helper == "class":
                return self.done({"raw": 21})
            return Done(outcome="done", output={"raw": 21})

    register_step(AliasedStep)
    workflow = load_workflow(document("entry", step=AliasedStep.key), key="aliased_result", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    row = system_queryset(StepRun).get(run=run)

    assert runner.execute(row.pk)

    retained = system_queryset(WorkflowRun).get(pk=run.pk)
    assert retained.status == RunStatus.SUCCEEDED
    assert retained.output == {"value": 22}
    assert validated == [21]


@pytest.mark.parametrize("phase", ["resolution", "binding", "input_write"])
def test_claim_followup_database_error_keeps_attempt(execution, monkeypatch, phase):
    """A real statement error after the attempt insert cannot poison failure recording."""
    actor, _ = execution
    workflow = load_workflow(document("entry"), key="claim_savepoint", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    row = system_queryset(StepRun).get(run=run)

    def invalid_statement(*args, **kwargs):
        Workflow.objects.filter(pk=workflow.pk).update(key=None)

    if phase == "input_write":
        update = StepRunQuerySet.update

        def fail_input_write(self, **kwargs):
            if "input" in kwargs:
                invalid_statement()
            return update(self, **kwargs)

        monkeypatch.setattr(StepRunQuerySet, "update", fail_input_write)
    else:
        monkeypatch.setattr(Definition, "step" if phase == "resolution" else "input_for", invalid_statement)

    assert runner.execute(row.pk)

    attempt = system_queryset(StepAttempt).get(step_run=row)
    assert attempt.number == 1 and attempt.result == "failed"
    assert "IntegrityError" in attempt.stacktrace
    assert system_queryset(StepRun).get(pk=row.pk).dispatches == 0
    assert system_queryset(WorkflowRun).get(pk=run.pk).status == RunStatus.FAILED


def test_outcome_routes_and_skips(execution):
    """Named outcomes settle the selected branch and retain explicit skipped evidence."""
    actor, _ = execution
    workflow = load_workflow(
        {
            "nodes": {
                "choose": {"step": "route", "config": {"outcome": "left"}, "next": {"left": "left", "right": "right"}},
                "left": {"step": "echo"},
                "right": {"step": "echo"},
            },
            "results": [{"from": "right"}, {"from": "left", "as": "selected"}],
        },
        key="branch",
        actor=actor,
    )
    run = WorkflowRun.objects.start(workflow, actor=actor, input={"value": 9})
    run_until(run)
    assert system_queryset(StepRun).get(run=run, node_key="right").status == StepRunStatus.SKIPPED
    assert system_queryset(WorkflowRun).get(pk=run.pk).outcome == "selected"


def test_draft_compare_and_swap_and_publish_hash(execution):
    """Draft revisions reject stale writes and unchanged publication reuses its version."""
    actor, _ = execution
    workflow = load_workflow(document("entry"), key="draft", actor=actor)
    revision = workflow.draft_revision
    incomplete = {"nodes": {"first": {"step": "echo"}, "second": {"step": "echo"}}}
    saved = Workflow.objects.save_draft(workflow, draft=incomplete, expected_revision=revision, actor=actor)
    assert saved.issues
    with pytest.raises(StaleRevisionError):
        Workflow.objects.save_draft(workflow, draft=document("entry"), expected_revision=revision, actor=actor)
    with pytest.raises(DefinitionInvalid):
        Workflow.objects.publish(
            workflow,
            expected_revision=system_queryset(Workflow).values_list("draft_revision", flat=True).get(pk=workflow.pk),
            actor=actor,
        )
    with pytest.raises(DefinitionInvalid):
        Workflow.objects.save_draft(
            workflow, draft=document("entry", step="missing"), expected_revision=saved.revision, actor=actor
        )
    Workflow.objects.save_draft(workflow, draft=document("entry"), expected_revision=saved.revision, actor=actor)
    original = workflow.published_id
    assert (
        Workflow.objects.publish(
            workflow,
            expected_revision=system_queryset(Workflow).values_list("draft_revision", flat=True).get(pk=workflow.pk),
            actor=actor,
        ).version.pk
        == original
    )
    assert system_queryset(WorkflowVersion).filter(workflow=workflow).count() == 1


def test_request_key_and_cancel(execution):
    """A keyed request is replayable until its operator cancels open work."""
    actor, _ = execution
    workflow = load_workflow(document("entry"), key="request", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor, input={"value": 1}, request_key="example:request")
    same = WorkflowRun.objects.start(workflow, actor=actor, input={"value": 1}, request_key="example:request")
    assert same.pk == run.pk
    with pytest.raises(Exception, match="different request"):
        WorkflowRun.objects.start(workflow, actor=actor, input={"value": 2}, request_key="example:request")
    WorkflowRun.objects.cancel(run, actor=actor)
    assert system_queryset(WorkflowRun).get(pk=run.pk).status == RunStatus.CANCELED
    row = system_queryset(StepRun).get(run=run)
    assert row.status == StepRunStatus.CANCELED
    assert not runner.execute(row.pk)


def test_cancel_preserves_failed_run_terminal_fields(execution, monkeypatch):
    """Cancel leaves a naturally failed run's persisted result and failure cause intact."""
    actor, _ = execution
    workflow = load_workflow(document("entry"), key="cancel_failed", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor, input={"value": 22})
    step_run = system_queryset(StepRun).get(run=run)

    def result_failure(*args, **kwargs):
        raise ValueError("Result assembly failed.")

    monkeypatch.setattr(Definition, "result_for", result_failure)
    assert runner.execute(step_run.pk)
    failed = system_queryset(WorkflowRun).get(pk=run.pk)
    original = (failed.status, failed.outcome, failed.output, failed.error, failed.finished_at)
    assert original[:4] == (RunStatus.FAILED, "error", {}, "Result assembly failed.")
    assert original[4] is not None

    WorkflowRun.objects.cancel(run, actor=actor)

    retained = system_queryset(WorkflowRun).get(pk=run.pk)
    assert (retained.status, retained.outcome, retained.output, retained.error, retained.finished_at) == original
    assert system_queryset(StepRun).get(pk=step_run.pk).status == StepRunStatus.SUCCEEDED
    assert system_queryset(StepAttempt).get(step_run=step_run).result == "succeeded"


def test_cancel_missing_run_returns_without_authorization_error(execution):
    """A stale run reference cannot turn an idempotent cancellation into an error."""
    actor, _ = execution
    missing = WorkflowRun(pk=-1)
    WorkflowRun.objects.cancel(missing, actor=actor)
    assert not system_queryset(WorkflowRun).filter(pk=missing.pk).exists()


@pytest.mark.parametrize("step_key", ["pause", "retry"])
def test_wait_and_retry_in_place(execution, step_key):
    """Time waits and retries reuse the same row with separately numbered attempts."""
    actor, _ = execution
    workflow = load_workflow(document("entry", step=step_key), key=step_key, actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    row = system_queryset(StepRun).get(run=run)
    assert runner.execute(row.pk)
    assert system_queryset(StepRun).get(pk=row.pk).status == StepRunStatus.WAITING
    assert runner.wake() == 1
    assert runner.execute(row.pk)
    row = system_queryset(StepRun).get(pk=row.pk)
    assert row.status == StepRunStatus.SUCCEEDED and row.attempt == 2 and row.retries == 0
    assert system_queryset(StepAttempt).filter(step_run=row).count() == 2


def test_error_edge_recovers(execution):
    """A permanent step failure can route through the declared domain error edge."""
    actor, _ = execution
    workflow = load_workflow(
        {
            "nodes": {
                "reject": {"step": "reject", "next": {"error": "recover"}},
                "recover": {"step": "echo", "input": {"value": {"value": 4}}},
            },
            "results": [{"from": "recover"}],
        },
        key="recovery",
        actor=actor,
    )
    run = WorkflowRun.objects.start(workflow, actor=actor)
    run_until(run)
    assert system_queryset(WorkflowRun).get(pk=run.pk).status == RunStatus.SUCCEEDED
    attempt = system_queryset(StepAttempt).get(step_run__run=run, step_run__node_key="reject")
    assert attempt.result == "failed" and attempt.error


def test_unrouted_failure_preserves_pending_branches(execution):
    """An unhandled failure closes the run while preserving resumable siblings."""
    actor, _ = execution
    workflow = load_workflow(
        {
            "nodes": {
                "entry": {"step": "echo", "next": {"done": ["bad", "other"]}},
                "bad": {"step": "reject"},
                "other": {"step": "echo"},
            }
        },
        key="failed",
        actor=actor,
    )
    run = WorkflowRun.objects.start(workflow, actor=actor)
    runner.execute(system_queryset(StepRun).get(run=run).pk)
    runner.execute(system_queryset(StepRun).get(run=run, node_key="bad").pk)
    assert system_queryset(WorkflowRun).get(pk=run.pk).status == RunStatus.FAILED
    assert system_queryset(StepRun).get(run=run, node_key="other").status == StepRunStatus.READY
    assert system_queryset(WorkflowRun).get(pk=run.pk).error == ""
    assert system_queryset(StepAttempt).get(step_run__run=run, step_run__node_key="bad").error


def test_dispatch_counter_and_exhaustion(execution, settings):
    """Only tick redeliveries consume the allowance before parking for an operator."""
    actor, sent = execution
    settings.ANGEE_WORKFLOW_MAX_DISPATCHES = 2
    workflow = load_workflow(document("entry"), key="delivery", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    row = system_queryset(StepRun).get(run=run)
    for expected in (1, 2):
        with system_context(reason="test.missing_delivery"):
            StepRun.objects.filter(pk=row.pk).update(dispatched_at=Now() - timedelta(seconds=61))
        assert runner.redispatch() == 1
        assert system_queryset(StepRun).get(pk=row.pk).dispatches == expected
    retained = system_queryset(WorkflowRun).get(pk=run.pk)
    assert retained.status == RunStatus.WAITING and retained.error == ""
    waiting = system_queryset(StepRun).get(pk=row.pk)
    assert waiting.waiting_kind == "error" and waiting.wait_reason
    assert len(sent) == 2


def test_body_rollback_for_returned_failure(execution, register_step):
    """Returning a failed settlement rolls back the body savepoint."""
    actor, _ = execution
    workflow = load_workflow(document("entry"), key="rollback", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)

    class RolledBackBody(Echo):
        def run(self, ctx):
            Workflow.objects.filter(pk=workflow.pk).update(name="Uncommitted")
            return ctx.fail("Roll it back.")

    register_step(RolledBackBody)
    runner.execute(system_queryset(StepRun).get(run=run).pk)
    assert system_queryset(Workflow).get(pk=workflow.pk).name != "Uncommitted"
    assert system_queryset(WorkflowRun).get(pk=run.pk).status == RunStatus.FAILED


@pytest.mark.parametrize("explicit_actor", [False, True])
def test_publication_and_start_keep_requesting_actor_attribution(execution, explicit_actor):
    """An explicit requester overrides the pinned actor; ambient context does not."""
    admin, _ = execution
    operator = create_user("publication_operator")
    workflow = load_workflow(document("entry"), key="attribution", actor=admin, publish=False)
    workflow.grant_record_access("editor", operator)
    workflow.grant_record_access("starter", operator)

    with actor_context(operator):
        actor = operator if explicit_actor else None
        version = Workflow.objects.publish(
            workflow,
            expected_revision=system_queryset(Workflow).values_list("draft_revision", flat=True).get(pk=workflow.pk),
            actor=actor,
        ).version
        run = WorkflowRun.objects.start(workflow, actor=actor)

    requester = operator if explicit_actor else admin
    assert version.published_by_id == requester.pk
    assert run.run_as_id == requester.pk


def test_start_requires_an_actor_even_with_system_access(execution):
    """System authorization alone cannot supply the principal of a new run."""
    actor, _ = execution
    workflow = load_workflow(document("entry"), key="missing_actor", actor=actor)
    workflow = Workflow._base_manager.get(pk=workflow.pk)

    with system_context(reason="test.start.without_actor"), pytest.raises(PermissionDenied, match="requires an actor"):
        WorkflowRun.objects.start(workflow, actor=None)


def test_workflow_verbs_deny_an_explicit_outsider_and_preserve_the_pinned_actor(execution):
    """K3 keeps the pinned requester unless the caller supplies an explicit actor."""
    admin, _ = execution
    workflow = load_workflow(document("entry"), key="ambient_requester", actor=admin)
    run = WorkflowRun.objects.start(workflow, actor=admin)
    run_until(run)
    outsider = create_user("ambient_outsider")

    with actor_context(outsider):
        with pytest.raises(PermissionDenied, match="'write'"):
            Workflow.objects.save_draft(
                workflow.with_actor(admin),
                draft=document("entry"),
                expected_revision=workflow.draft_revision,
                actor=outsider,
            )
        with pytest.raises(PermissionDenied, match="'write'"):
            Workflow.objects.publish(
                workflow.with_actor(admin), expected_revision=workflow.draft_revision, actor=outsider
            )
        with pytest.raises(PermissionDenied, match="'start'"):
            WorkflowRun.objects.start(workflow.with_actor(admin), actor=outsider)
        with pytest.raises(PermissionDenied, match="'write'"):
            WorkflowRun.objects.reprocess(run.with_actor(admin), actor=outsider)
        with pytest.raises(PermissionDenied, match="'write'"):
            WorkflowRun.objects.cancel(run.with_actor(admin), actor=outsider)
        published = Workflow.objects.publish(
            workflow.with_actor(admin), expected_revision=workflow.draft_revision
        ).version
        started = WorkflowRun.objects.start(workflow.with_actor(admin), actor=None)
        replay = WorkflowRun.objects.reprocess(run.with_actor(admin))
        assert published.published_by_id == started.run_as_id == replay.run_as_id == admin.pk


@pytest.mark.parametrize("strict_mode", [False, True])
def test_workflow_verbs_deny_missing_requesters_in_every_strict_mode(execution, settings, strict_mode):
    """Every public workflow mutation fails closed before a scoped ORM query."""
    admin, _ = execution
    workflow = load_workflow(document("entry"), key="actorless_verbs", actor=admin)
    run = WorkflowRun.objects.start(workflow, actor=admin)
    run_until(run)
    workflow = Workflow._base_manager.get(pk=workflow.pk)
    run = WorkflowRun._base_manager.get(pk=run.pk)
    settings.REBAC_STRICT_MODE = strict_mode
    operations = (
        lambda: Workflow.objects.save_draft(workflow, draft=document("entry"), expected_revision=0),
        lambda: Workflow.objects.publish(
            workflow,
            expected_revision=system_queryset(Workflow).values_list("draft_revision", flat=True).get(pk=workflow.pk),
        ),
        lambda: WorkflowRun.objects.start(workflow, actor=None),
        lambda: WorkflowRun.objects.cancel(run),
        lambda: WorkflowRun.objects.reprocess(run),
        lambda: Workflow.objects.install_definition(key=workflow.key, name=workflow.name, draft=document("entry")),
        lambda: Workflow.objects.install_definition(key="actorless_new", name="New", draft=document("entry")),
    )
    for operation in operations:
        with pytest.raises(PermissionDenied, match="requires an actor"):
            operation()


def test_constraints_and_actor_admission(execution):
    """State constraints and workflow start permissions remain enforced."""
    actor, _ = execution
    workflow = load_workflow(document("entry"), key="constraints", actor=actor)
    with pytest.raises(PermissionDenied, match="'start'"):
        WorkflowRun.objects.start(workflow, actor=create_user("outsider"))
    run = WorkflowRun.objects.start(workflow, actor=actor)
    row = system_queryset(StepRun).get(run=run)
    with pytest.raises(IntegrityError), transaction.atomic(), system_context(reason="test.invalid_state"):
        StepRun.objects.filter(pk=row.pk).update(status="running")
    with system_context(reason="test.invalid_body_context"):
        assert runner.execute(row.pk)
    attempt = system_queryset(StepAttempt).get(step_run=row)
    assert attempt.result == "failed" and "outside system_context" in attempt.error
    with actor_context(actor):
        assert WorkflowRun.objects.filter(pk=run.pk).exists()


def test_attempt_result_and_wait_companions_are_database_invariants(execution):
    """Direct writes cannot retain an impossible attempt or wait shape."""
    actor, _ = execution
    workflow = load_workflow(document("entry"), key="settlement_constraints", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    row = system_queryset(StepRun).get(run=run)
    invalid_waits = (
        {"status": StepRunStatus.WAITING, "waiting_kind": WaitingKind.TIME},
        {"status": StepRunStatus.WAITING, "waiting_kind": WaitingKind.RECORD, "wait_reason": "bad"},
        {"status": StepRunStatus.WAITING, "waiting_kind": WaitingKind.RUN},
        {"wake_at": Now()},
    )
    for values in invalid_waits:
        with pytest.raises(IntegrityError), transaction.atomic(), system_context(reason="test.wait_constraint"):
            StepRun.objects.filter(pk=row.pk).update(**values)
    assert runner.execute(row.pk)
    attempt = system_queryset(StepAttempt).get(step_run=row)
    for values in ({"result": None}, {"finished_at": None}):
        with pytest.raises(IntegrityError), transaction.atomic(), system_context(reason="test.attempt_constraint"):
            StepAttempt.objects.filter(pk=attempt.pk).update(**values)


def test_holder_resend_refreshes_backlogged_ready_row_before_tick(execution, settings):
    """A holder re-send refreshes a five-minute-old message without spending a retry."""
    actor, sent = execution
    settings.ANGEE_WORKFLOW_MAX_DISPATCHES = 5
    workflow = load_workflow(document("entry"), key="backlog", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    step_run = system_queryset(StepRun).get(run=run)
    with system_context(reason="test.five_minute_backlog"):
        StepRun.objects.filter(pk=step_run.pk).update(
            dispatched_at=Now() - timedelta(minutes=5), dispatches=4,
        )
    with WorkflowRun.objects.hold(run.pk):
        pass
    assert runner.redispatch() == 0
    retained = system_queryset(StepRun).get(pk=step_run.pk)
    assert retained.status == StepRunStatus.READY and retained.dispatches == 4
    assert retained.dispatched_at > step_run.dispatched_at
    assert system_queryset(WorkflowRun).get(pk=run.pk).status == RunStatus.RUNNING
    assert len(sent) == 2
    assert runner.execute(step_run.pk)
    assert system_queryset(WorkflowRun).get(pk=run.pk).status == RunStatus.SUCCEEDED


def test_nested_holds_dispatch_each_ready_row_once(execution):
    """The outer hold owns one delivery callback for a nested run lock."""
    actor, sent = execution
    workflow = load_workflow(document("entry"), key="nested_dispatch", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    sent.clear()

    with WorkflowRun.objects.hold(run.pk):
        with WorkflowRun.objects.hold(run.pk):
            assert system_queryset(StepRun).filter(run=run, status=StepRunStatus.READY).count() == 1

    assert [name for name, _ in sent] == ["workflows.execute"]


def test_t19_result_binding_error_preserves_successful_body(execution, register_step, monkeypatch, caplog):
    """A result projection defect fails the run durably after the final body commits."""
    actor, _ = execution
    workflow = load_workflow(document("entry"), key="result_failure", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor, input={"value": 19})
    step_run = system_queryset(StepRun).get(run=run)

    def projection_error(self, rows, run_input):
        raise DjangoValidationError("Injected result binding failure.")

    register_step(RetainedBody)
    monkeypatch.setattr(Definition, "result_for", projection_error)
    assert runner.execute(step_run.pk)
    retained = system_queryset(WorkflowRun).get(pk=run.pk)
    assert (retained.status, retained.outcome, retained.output) == (RunStatus.FAILED, "error", {})
    assert "Injected result binding failure" in retained.error
    assert retained.error == "Injected result binding failure."
    assert "Workflow run" in caplog.text and "Injected result binding failure" in caplog.text
    assert system_queryset(Workflow).get(pk=workflow.pk).name == "Successful body retained"
    assert system_queryset(StepRun).get(pk=step_run.pk).status == StepRunStatus.SUCCEEDED
    assert system_queryset(StepAttempt).get(step_run=step_run).result == "succeeded"
    assert system_queryset(StepAttempt).get(step_run=step_run).error == ""
    assert not runner.execute(step_run.pk)


def test_settlement_error_is_recorded_on_attempt_once(execution, monkeypatch):
    actor, _ = execution
    workflow = load_workflow(document("entry"), key="settlement_error_place", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    row = system_queryset(StepRun).get(run=run)
    original = StepRunQuerySet.settle
    calls = 0

    def fail_once(self, step_run, settlement):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise DataError("invalid result")
        return original(self, step_run, settlement)

    monkeypatch.setattr(StepRunQuerySet, "settle", fail_once)
    assert runner.execute(row.pk)
    assert system_queryset(WorkflowRun).get(pk=run.pk).error == ""
    assert system_queryset(StepAttempt).get(step_run=row).error == "DataError: invalid result"


def test_reprocess_uses_current_publication_and_ambient_actor(execution):
    """Reprocess has fresh identity on the current graph, preserving input and subject."""
    actor, _ = execution
    workflow = load_workflow(document("entry"), key="reprocess", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor, input={"value": 8}, request_key="test:reprocess")
    run_until(system_queryset(WorkflowRun).get(pk=run.pk))
    Workflow.objects.save_draft(
        workflow,
        draft=document("entry", "last"),
        expected_revision=workflow.draft_revision,
        actor=actor,
    )
    current = Workflow.objects.publish(
        workflow,
        expected_revision=system_queryset(Workflow).values_list("draft_revision", flat=True).get(pk=workflow.pk),
        actor=actor,
    ).version
    with actor_context(actor):
        replay = WorkflowRun.objects.reprocess(run)
    assert replay.pk != run.pk and replay.version_id == current.pk != run.version_id
    assert replay.input == run.input and replay.request_key is None
    assert replay.subject_content_type_id == run.subject_content_type_id
    assert replay.subject_object_id == run.subject_object_id
    assert replay.run_as_id == actor.pk


def test_run_version_remains_pinned_across_republish(execution):
    actor, _ = execution
    workflow = load_workflow(document("entry"), key="version_pin", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor, input={"value": 8}, request_key="test:version_pin")
    pinned = run.version_id
    Workflow.objects.save_draft(
        workflow,
        draft=document("entry", "last"),
        expected_revision=workflow.draft_revision,
        actor=actor,
    )
    current = Workflow.objects.publish(
        workflow,
        expected_revision=system_queryset(Workflow).values_list("draft_revision", flat=True).get(pk=workflow.pk),
        actor=actor,
    ).version
    assert current.pk != pinned
    assert (
        WorkflowRun.objects.start(
            workflow,
            actor=actor,
            input={"value": 8},
            request_key="test:version_pin",
        ).pk
        == run.pk
    )
    run_until(run)
    retained = system_queryset(WorkflowRun).get(pk=run.pk)
    assert retained.version_id == pinned and retained.status == RunStatus.SUCCEEDED
    assert system_queryset(StepRun).filter(run=run).count() == 1


@pytest.mark.parametrize("step", ["echo", "pause"])
def test_reprocess_rejects_nonterminal_runs(execution, step):
    """An operator cannot admit a second run while the original is running or waiting."""
    actor, _ = execution
    workflow = load_workflow(document("entry", step=step), key="active_reprocess", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    if step == "pause":
        assert runner.execute(system_queryset(StepRun).get(run=run).pk)
    run.status = RunStatus.FAILED
    with pytest.raises(DjangoValidationError, match="terminal"):
        WorkflowRun.objects.reprocess(run, actor=actor)
    assert system_queryset(WorkflowRun).filter(version__workflow=workflow).count() == 1


@pytest.mark.parametrize("run_access,workflow_access", [(False, True), (True, False), (True, True)])
def test_reprocess_requires_operator_access_and_acts_as_requester(execution, run_access, workflow_access):
    """Reprocessing needs both capabilities and records the requesting operator as principal."""
    admin, _ = execution
    operator = create_user("reprocess_operator")
    workflow = load_workflow(document("entry"), key="operator_reprocess", actor=admin)
    run = WorkflowRun.objects.start(workflow, actor=admin)
    run_until(run)
    if run_access:
        run.with_actor(admin).grant_record_access("operator", operator)
    if workflow_access:
        workflow.with_actor(admin).grant_record_access("starter", operator)
    if not run_access or not workflow_access:
        with pytest.raises(PermissionDenied, match="write" if not run_access else "start"):
            WorkflowRun.objects.reprocess(run, actor=operator)
        assert system_queryset(WorkflowRun).filter(version__workflow=workflow).count() == 1
    else:
        with actor_context(operator):
            replay = WorkflowRun.objects.reprocess(run.with_actor(operator))
        assert replay.pk != run.pk and replay.run_as_id == operator.pk != run.run_as_id
        assert replay.version_id == run.version_id and replay.request_key is None


def test_failed_attempt_retains_bound_input_and_database_start_time(execution, register_step):
    """The body savepoint never rolls back the attempt's bound input or clock."""
    actor, _ = execution
    workflow = load_workflow(document("entry", "last"), key="retained_input", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor, input={"value": 23})
    assert runner.execute(system_queryset(StepRun).get(run=run).pk)
    last = system_queryset(StepRun).get(run=run, node_key="last")

    class FailedLastStep(Echo):
        def run(self, ctx):
            assert ctx.now == ctx.attempt.started_at
            assert ctx.step_run.input == {"value": 23}
            return ctx.fail("Keep my input.")

    register_step(FailedLastStep)
    assert runner.execute(last.pk)
    retained = system_queryset(StepRun).get(pk=last.pk)
    assert retained.input == {"value": 23} and retained.status == StepRunStatus.FAILED
    assert system_queryset(StepAttempt).get(step_run=last).error == "Keep my input."


def test_publish_precedes_failing_commit_send(execution, caplog):
    """One explicit run publication survives a logged commit-time enqueue failure."""
    actor, _ = execution
    workflow = load_workflow(document("entry", "last"), key="publish_before_send", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    step_run = system_queryset(StepRun).get(run=run)
    with observe(WorkflowRun) as published, capture_tasks(error=RuntimeError("Broker unavailable.")):
        assert runner.execute(step_run.pk)
    assert len(published) == 1
    assert "Broker unavailable" in caplog.text
    assert system_queryset(StepRun).get(pk=step_run.pk).status == StepRunStatus.SUCCEEDED
    assert system_queryset(StepRun).get(run=run, node_key="last").status == StepRunStatus.READY


@pytest.mark.parametrize("sqlstate", ["57014", "40P01", "55P03"])
def test_retryable_database_failure_records_attempt_and_schedules_retry(execution, register_step, sqlstate):
    """Timeout, deadlock and unavailable-lock SQLSTATEs use the step's retry owner."""
    actor, _ = execution
    workflow = load_workflow(document("entry"), key=f"sqlstate_{sqlstate}", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor, input={"value": 5})

    class RetryingDatabaseFailure(Echo):
        retry = RetryPolicy(max_attempts=2, backoff=timedelta())

        def run(self, ctx):
            Workflow.objects.filter(pk=workflow.pk).update(name="Rolled back")
            raise OperationalError("Transient database failure.") from psycopg.errors.lookup(sqlstate)(
                "Injected failure."
            )

    register_step(RetryingDatabaseFailure)
    step_run = system_queryset(StepRun).get(run=run)
    assert runner.execute(step_run.pk)
    retained = system_queryset(StepRun).get(pk=step_run.pk)
    assert retained.status == StepRunStatus.WAITING and retained.retries == 1
    assert retained.input == {"value": 5}
    attempt = system_queryset(StepAttempt).get(step_run=step_run)
    assert attempt.result == "failed" and "Transient database failure" in attempt.error
    assert system_queryset(Workflow).get(pk=workflow.pk).name != "Rolled back"


def test_soft_time_limit_records_timed_out_and_keeps_input(execution, register_step):
    """A Celery soft timeout rolls back body writes and records its distinct result."""
    actor, _ = execution
    workflow = load_workflow(document("entry"), key="soft_limit", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor, input={"value": 14})

    class TimedOutBody(Echo):
        def run(self, ctx):
            Workflow.objects.filter(pk=workflow.pk).update(name="Rolled back")
            raise SoftTimeLimitExceeded()

    register_step(TimedOutBody)
    step_run = system_queryset(StepRun).get(run=run)
    assert runner.execute(step_run.pk)
    retained = system_queryset(StepRun).get(pk=step_run.pk)
    assert retained.input == {"value": 14} and retained.status == StepRunStatus.FAILED
    assert system_queryset(StepAttempt).get(step_run=step_run).result == "timed_out"
    assert system_queryset(Workflow).get(pk=workflow.pk).name != "Rolled back"


def test_start_permission_does_not_grant_workflow_editing(execution):
    """A starter can list and read published graphs without gaining editing rights."""
    admin, _ = execution
    actor = create_user("starter")
    workflow = load_workflow(document("entry"), key="start_permission", actor=admin)
    workflow.with_actor(admin).grant_record_access("starter", actor)
    assert list(Workflow.objects.with_actor(actor).values_list("pk", flat=True)) == [workflow.pk]
    assert Workflow.objects.with_actor(actor).get(pk=workflow.pk).name == workflow.name
    assert WorkflowVersion.objects.with_actor(actor).get(pk=workflow.published_id).workflow_id == workflow.pk
    run = WorkflowRun.objects.start(workflow, actor=actor)
    assert run.run_as_id == actor.pk
    with pytest.raises(PermissionDenied):
        Workflow.objects.save_draft(
            workflow,
            actor=actor,
            draft=document("entry"),
            expected_revision=workflow.draft_revision,
        )
    with pytest.raises(PermissionDenied):
        Workflow.objects.publish(
            workflow,
            expected_revision=system_queryset(Workflow).values_list("draft_revision", flat=True).get(pk=workflow.pk),
            actor=actor,
        )
    with pytest.raises(PermissionDenied):
        Workflow.objects.install_definition(key=workflow.key, name=workflow.name, draft=document("entry"), actor=actor)
    WorkflowRun.objects.cancel(run, actor=actor)
    assert system_queryset(WorkflowRun).get(pk=run.pk).status == RunStatus.CANCELED
    replay = WorkflowRun.objects.reprocess(run, actor=actor)
    assert replay.pk != run.pk and replay.run_as_id == actor.pk

    other = create_user("another_starter")
    workflow.with_actor(admin).grant_record_access("starter", other)
    other_run = WorkflowRun.objects.start(workflow, actor=other)
    with pytest.raises(PermissionDenied, match="write"):
        WorkflowRun.objects.cancel(other_run, actor=actor)
    WorkflowRun.objects.cancel(other_run, actor=other)
    with pytest.raises(PermissionDenied, match="write"):
        WorkflowRun.objects.reprocess(other_run, actor=actor)


@pytest.mark.parametrize("storage", ["denormalized", "registry"])
def test_starter_cannot_read_another_starters_engine_rows(execution, settings, storage):
    """Identity and publication visibility do not expose another user's execution."""
    settings.REBAC_LOCAL_BACKEND_STORAGE = storage
    call_command("rebac", "sync", verbosity=0)
    admin, _ = execution
    with system_context(reason="test workflow storage administrator"):
        grant_role(actor=admin, role="angee/role:admin")
    first, second = create_user("first_starter"), create_user("second_starter")
    workflow = load_workflow(document("entry"), key="private_runs", actor=admin)
    for actor in (first, second):
        workflow.require_access("write", admin)
        workflow.grant_record_access("starter", actor)
    first_run = WorkflowRun.objects.start(workflow, actor=first)
    second_run = WorkflowRun.objects.start(workflow, actor=second)
    run_until(first_run)
    run_until(second_run)
    for model in (WorkflowRun, StepRun, StepAttempt):
        other = system_queryset(model).get(**(
            {"pk": second_run.pk} if model is WorkflowRun else
            {"run": second_run} if model is StepRun else {"step_run__run": second_run}
        ))
        assert not model.objects.with_actor(first).filter(pk=other.pk).exists()
        with pytest.raises(model.DoesNotExist):
            model.objects.with_actor(first).get(pk=other.pk)
        with pytest.raises(PermissionDenied):
            other.require_access("read", first)
    visible = Workflow.objects.with_actor(first).get(pk=workflow.pk)
    assert visible.name == workflow.name
    assert visible.denied_read_fields() == frozenset({"draft", "layout", "draft_revision"})
    assert visible.draft is None and visible.layout is None and visible.draft_revision is None
    assert WorkflowVersion.objects.with_actor(first).get(pk=workflow.published_id).document


@pytest.mark.parametrize("model", [StepRun, StepAttempt])
def test_run_actor_cannot_create_engine_records(execution, model):
    """Engine record creation belongs to the engine even for the run's actor."""
    admin, _ = execution
    actor = create_user("engine_row_creator")
    workflow = load_workflow(document("entry"), key="engine_create", actor=admin)
    workflow.grant_record_access("starter", actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    row = system_queryset(StepRun).get(run=run)
    fields = {"run": run, "node_key": "forged"} if model is StepRun else {"step_run": row, "number": 1}
    with pytest.raises(PermissionDenied), transaction.atomic():
        model.objects.with_actor(actor).create(**fields)


@pytest.mark.parametrize("model,field,value", [(StepRun, "state", {"edited": True}), (StepAttempt, "error", "Edited")])
def test_run_actor_cannot_write_engine_records(execution, model, field, value):
    """Generic ORM writes cannot grant a run actor control of engine-owned rows."""
    admin, _ = execution
    actor = create_user("run_actor")
    workflow = load_workflow(document("entry"), key="engine_records", actor=admin)
    workflow.with_actor(admin).grant_record_access("starter", actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    run_until(run)
    row = model.objects.with_actor(actor).get()
    assert not row.with_actor(actor).has_access("write")
    with pytest.raises(PermissionDenied), transaction.atomic():
        model.objects.with_actor(actor).filter(pk=row.pk).update(**{field: value})
    setattr(row, field, value)
    with pytest.raises(PermissionDenied), transaction.atomic():
        row.with_actor(actor).save(update_fields=(field,))


def test_tick_bad_candidate_does_not_abort_later_candidates(execution, monkeypatch, caplog):
    """Each due row is isolated so a failed transition leaves later wakes usable."""
    actor, _ = execution
    workflow = load_workflow(document("entry", step="pause"), key="tick_isolation", actor=actor)
    runs = [WorkflowRun.objects.start(workflow, actor=actor) for _ in range(2)]
    step_runs = [system_queryset(StepRun).get(run=run) for run in runs]
    for step_run in step_runs:
        assert runner.execute(step_run.pk)
    original = StepRunQuerySet.to_ready

    def wake(self):
        if self.filter(pk=step_runs[0].pk).exists():
            raise RuntimeError("Injected candidate failure.")
        return original(self)

    monkeypatch.setattr(StepRunQuerySet, "to_ready", wake)
    assert runner.wake() == 1
    assert "Injected candidate failure" in caplog.text
    assert system_queryset(StepRun).get(pk=step_runs[0].pk).status == StepRunStatus.WAITING
    assert system_queryset(StepRun).get(pk=step_runs[1].pk).status == StepRunStatus.READY


def test_tick_superseded_candidate_does_not_abort_later_candidates(execution, monkeypatch):
    actor, _ = execution
    workflow = load_workflow(document("entry", step="pause"), key="tick_superseded", actor=actor)
    runs = [WorkflowRun.objects.start(workflow, actor=actor) for _ in range(2)]
    rows = [system_queryset(StepRun).get(run=run) for run in runs]
    for row in rows:
        assert runner.execute(row.pk)
    original = StepRunQuerySet.to_ready

    def wake(self, **kwargs):
        if self.filter(pk=rows[0].pk).exists():
            raise Superseded("This candidate lost its fence.")
        return original(self, **kwargs)

    monkeypatch.setattr(StepRunQuerySet, "to_ready", wake)
    assert runner.wake() == 1
    assert system_queryset(StepRun).get(pk=rows[1].pk).status == StepRunStatus.READY


def test_operator_retry_resets_delivery_allowance(execution, settings):
    actor, _ = execution
    settings.ANGEE_WORKFLOW_MAX_DISPATCHES = 2
    workflow = load_workflow(document("entry"), key="retry_delivery_allowance", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    row = system_queryset(StepRun).get(run=run)
    for _ in range(2):
        with system_context(reason="test exhausted delivery"):
            StepRun.objects.filter(pk=row.pk).update(dispatched_at=Now() - timedelta(seconds=61))
        assert runner.redispatch() == 1
    assert system_queryset(StepRun).get(pk=row.pk).waiting_kind == "error"
    ready = StepRun.objects.retry_step(row, actor=actor)
    assert ready.dispatches == 0


def test_t18_five_minute_backlog_does_not_exhaust_dispatches(execution):
    """A queued step behind five minutes of other runs must remain executable."""
    actor, _ = execution
    workflow = load_workflow(document("entry"), key="five_minute_backlog", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    step_run = system_queryset(StepRun).get(run=run)
    for _ in range(5):
        with system_context(reason="test.minute_of_worker_backlog"):
            StepRun.objects.filter(pk=step_run.pk).update(dispatched_at=Now() - timedelta(seconds=61))
        assert runner.redispatch() == 1
    retained = system_queryset(WorkflowRun).get(pk=run.pk)
    assert retained.status == RunStatus.RUNNING, (
        "Five minutes of worker backlog must fit within the default redelivery allowance."
    )
    assert runner.execute(step_run.pk)
    assert system_queryset(WorkflowRun).get(pk=run.pk).status == RunStatus.SUCCEEDED


def test_default_redelivery_exhaustion_waits_without_domain_error_routing(execution):
    """Twenty redeliveries exhaust the default allowance without inventing an attempt."""
    actor, sent = execution
    workflow = load_workflow(
        {
            "nodes": {
                "entry": {"step": "echo", "next": {"error": "recover"}},
                "recover": {"step": "echo", "input": {"value": {"value": 1}}},
            },
            "results": [{"from": "recover"}],
        },
        key="default_exhaustion", actor=actor,
    )
    run = WorkflowRun.objects.start(workflow, actor=actor)
    step_run = system_queryset(StepRun).get(run=run)
    for count in range(1, 21):
        with system_context(reason="test.undelivered_message"):
            StepRun.objects.filter(pk=step_run.pk).update(dispatched_at=Now() - timedelta(seconds=61))
        assert runner.redispatch() == 1
        assert system_queryset(StepRun).get(pk=step_run.pk).dispatches == count
        if count < 20:
            assert system_queryset(WorkflowRun).get(pk=run.pk).status == RunStatus.RUNNING
    retained = system_queryset(WorkflowRun).get(pk=run.pk)
    assert (retained.status, retained.outcome, retained.output, retained.error) == (RunStatus.WAITING, "", {}, "")
    retained_step_run = system_queryset(StepRun).get(pk=step_run.pk)
    assert retained_step_run.status == StepRunStatus.WAITING and retained_step_run.waiting_kind == "error"
    assert "delivery exhausted" in retained_step_run.wait_reason.lower()
    assert retained_step_run.state == {}
    assert retained_step_run.outcome == ""
    assert not system_queryset(StepRun).filter(run=run, node_key="recover").exists()
    assert not system_queryset(StepAttempt).filter(step_run=step_run).exists()
    assert len(sent) == 20


def test_nullable_whole_successor_input_fails_its_attempt_durably(execution, register_step):
    """A null whole binding keeps predecessor work and never poisons the claim transaction."""
    actor, _ = execution

    class OptionalPayload(BaseModel):
        """A valid producer object whose selected field is explicitly null."""

        payload: None = None

    class NullableProducer(Step[None, OptionalPayload, None]):
        """Write domain state and return the nullable source field."""

        key = "nullable_producer"

        def run(self, ctx):
            """Retain this body write when the successor's binding is invalid."""
            Workflow.objects.filter(pk=ctx.run.version.workflow_id).update(name="Retained producer write")
            return ctx.done({"payload": None})

    class UnreachableConsumer(Step[None, None, None]):
        """A nullable whole input must fail before this body starts."""

        key = "unreachable_consumer"

        def run(self, ctx):
            """Fail the proof if the invalid whole input reaches a body."""
            raise AssertionError("A whole null input reached the body.")

    register_step(NullableProducer)
    register_step(UnreachableConsumer)
    workflow = load_workflow(
        {
            "nodes": {
                "producer": {"step": NullableProducer.key, "next": {"done": "consumer"}},
                "consumer": {
                    "step": UnreachableConsumer.key,
                    "input": {"from": "producer", "path": ["payload"]},
                },
            },
        },
        key="nullable_binding", actor=actor,
    )
    run = WorkflowRun.objects.start(workflow, actor=actor)
    assert runner.execute(system_queryset(StepRun).get(run=run).pk)
    consumer = system_queryset(StepRun).get(run=run, node_key="consumer")
    assert runner.execute(consumer.pk)
    retained = system_queryset(WorkflowRun).get(pk=run.pk)
    assert (retained.status, retained.outcome, retained.output) == (RunStatus.FAILED, "error", {})
    assert system_queryset(Workflow).get(pk=workflow.pk).name == "Retained producer write"
    assert system_queryset(StepRun).get(run=run, node_key="producer").status == StepRunStatus.SUCCEEDED
    consumer = system_queryset(StepRun).get(pk=consumer.pk)
    assert consumer.status == StepRunStatus.FAILED and consumer.input == {} and consumer.attempt == 1
    attempt = system_queryset(StepAttempt).get(step_run=consumer)
    assert attempt.result == "failed" and "null" in attempt.error.lower()


def test_t22_removed_step_class_fails_first_attempt(execution):
    """A class removed after publication records its real resolution failure on first delivery."""
    actor, _ = execution

    class RemovedStep(Echo):
        """A published implementation that is no longer installed at execution."""

        key = "removed_step"

    with register_steps(RemovedStep):
        workflow = load_workflow(document("entry", step=RemovedStep.key), key="removed_class", actor=actor)
        run = WorkflowRun.objects.start(workflow, actor=actor)
        step_run = system_queryset(StepRun).get(run=run)

    assert runner.execute(step_run.pk)
    retained = system_queryset(StepRun).get(pk=step_run.pk)
    assert retained.status == StepRunStatus.FAILED and retained.attempt == 1 and retained.dispatches == 0
    attempt = system_queryset(StepAttempt).get(step_run=step_run)
    assert attempt.number == 1 and attempt.result == "failed" and attempt.finished_at is not None
    assert "No impl for key 'removed_step'" in attempt.error
    assert "ImproperlyConfigured" in attempt.stacktrace
    retained_run = system_queryset(WorkflowRun).get(pk=run.pk)
    assert retained_run.status == RunStatus.FAILED and retained_run.error == ""
    assert not runner.execute(step_run.pk)


@pytest.mark.parametrize("phase", ["input", "result"])
def test_soft_time_limit_outside_body_records_its_owner(execution, monkeypatch, phase):
    """Timeouts around the body preserve the attempt's actual execution result."""
    actor, _ = execution
    workflow = load_workflow(document("entry"), key=f"{phase}_timeout", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    step_run = system_queryset(StepRun).get(run=run)

    def timeout(*args, **kwargs):
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(Definition, "input_for" if phase == "input" else "result_for", timeout)
    assert runner.execute(step_run.pk)
    retained = system_queryset(StepRun).get(pk=step_run.pk)
    assert retained.status == (StepRunStatus.FAILED if phase == "input" else StepRunStatus.SUCCEEDED)
    assert retained.attempt == 1
    attempt = system_queryset(StepAttempt).get(step_run=step_run)
    assert attempt.result == ("timed_out" if phase == "input" else "succeeded")
    assert attempt.finished_at is not None
    retained_run = system_queryset(WorkflowRun).get(pk=run.pk)
    assert retained_run.status == RunStatus.FAILED
    if phase == "input":
        assert "SoftTimeLimitExceeded" in attempt.error and retained_run.error == ""
    else:
        assert attempt.error == "" and retained_run.error == "SoftTimeLimitExceeded()"


@pytest.mark.parametrize("phase", ["body", "result"])
def test_failure_text_strips_nul_and_respects_field_bounds(execution, register_step, monkeypatch, phase):
    """Diagnostic text is sanitized and truncated to each declared column bound."""
    actor, _ = execution
    workflow = load_workflow(document("entry"), key=f"nul_{phase}", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    step_run = system_queryset(StepRun).get(run=run)

    def fail(*args, **kwargs):
        raise ValueError("Invalid\x00failure text" + "x" * 70000)

    if phase == "body":
        class InvalidTextBody(Echo):
            def run(self, ctx):
                fail()

        register_step(InvalidTextBody)
    else:
        monkeypatch.setattr(Definition, "result_for", fail)
    assert runner.execute(step_run.pk)
    retained_run = system_queryset(WorkflowRun).get(pk=run.pk)
    attempt = system_queryset(StepAttempt).get(step_run=step_run)
    assert retained_run.status == RunStatus.FAILED
    assert "\x00" not in retained_run.error + attempt.error + attempt.stacktrace
    expected = "Invalidfailure text" + "x" * 70000
    if phase == "body":
        assert attempt.result == "failed"
        assert attempt.error == expected[:StepAttempt._meta.get_field("error").max_length]
        assert system_queryset(StepRun).get(pk=step_run.pk).output == {"error": attempt.error}
        assert len(attempt.stacktrace) == StepAttempt._meta.get_field("stacktrace").max_length
        assert retained_run.error == ""
    else:
        assert attempt.result == "succeeded" and attempt.error == ""
        assert retained_run.error == expected[:WorkflowRun._meta.get_field("error").max_length]


@pytest.mark.parametrize("failure_write", ["state", "publication"])
def test_failure_recording_database_errors_preserve_successful_body(
    execution, register_step, monkeypatch, caplog, failure_write,
):
    """Real statement errors in recovery writes roll back only their individual savepoints."""
    actor, _ = execution
    workflow = load_workflow(document("entry"), key=f"failure_{failure_write}", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    step_run = system_queryset(StepRun).get(run=run)

    def result_error(*args, **kwargs):
        raise ValueError("Injected result failure.")

    def invalid_write(*args, **kwargs):
        Workflow.objects.filter(pk=workflow.pk).update(key=None)

    register_step(RetainedBody)
    monkeypatch.setattr(Definition, "result_for", result_error)
    if failure_write == "state":
        monkeypatch.setattr(WorkflowRunManager, "_write_state", invalid_write)
    else:
        monkeypatch.setattr(runner_module, "publish_change", invalid_write)
    assert runner.execute(step_run.pk)
    assert system_queryset(Workflow).get(pk=workflow.pk).name == "Successful body retained"
    assert system_queryset(StepRun).get(pk=step_run.pk).status == StepRunStatus.SUCCEEDED
    attempt = system_queryset(StepAttempt).get(step_run=step_run)
    assert attempt.result == "succeeded" and attempt.error == ""
    assert any(record.exc_info and isinstance(record.exc_info[1], IntegrityError) for record in caplog.records)
    if failure_write == "publication":
        retained_run = system_queryset(WorkflowRun).get(pk=run.pk)
        assert retained_run.status == RunStatus.FAILED and retained_run.error == "Injected result failure."


def test_failure_attempt_close_database_error_preserves_successful_body(execution, register_step, monkeypatch, caplog):
    """A broken attempt close after settlement failure cannot poison committed domain work."""
    actor, _ = execution
    workflow = load_workflow(document("entry"), key="failure_attempt_close", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    step_run = system_queryset(StepRun).get(run=run)

    def settlement_error(*args, **kwargs):
        raise ValueError("Injected settlement failure.")

    def attempt_close(self, result, error="", stacktrace=""):
        Workflow.objects.filter(pk=workflow.pk).update(key=None)

    register_step(RetainedBody)
    monkeypatch.setattr(StepRunQuerySet, "settle", settlement_error)
    monkeypatch.setattr(StepAttemptQuerySet, "close", attempt_close)
    assert runner.execute(step_run.pk)
    assert system_queryset(Workflow).get(pk=workflow.pk).name == "Successful body retained"
    retained_run = system_queryset(WorkflowRun).get(pk=run.pk)
    assert retained_run.status == RunStatus.FAILED and retained_run.error == "Injected settlement failure."
    assert system_queryset(StepAttempt).get(step_run=step_run).finished_at is None
    assert any(record.exc_info and isinstance(record.exc_info[1], IntegrityError) for record in caplog.records)


def test_soft_time_limit_during_settlement_belongs_to_open_attempt(execution, register_step, monkeypatch):
    """A timeout before closing the invocation stays on that invocation's attempt."""
    actor, _ = execution
    workflow = load_workflow(document("entry"), key="settlement_timeout", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    step_run = system_queryset(StepRun).get(run=run)

    def timeout(*args, **kwargs):
        raise SoftTimeLimitExceeded()

    register_step(RetainedBody)
    monkeypatch.setattr(StepRunQuerySet, "settle", timeout)
    assert runner.execute(step_run.pk)
    assert system_queryset(Workflow).get(pk=workflow.pk).name == "Successful body retained"
    attempt = system_queryset(StepAttempt).get(step_run=step_run)
    assert attempt.result == "timed_out" and attempt.finished_at is not None
    assert "SoftTimeLimitExceeded" in attempt.error and "SoftTimeLimitExceeded" in attempt.stacktrace
    retained_run = system_queryset(WorkflowRun).get(pk=run.pk)
    assert retained_run.status == RunStatus.FAILED and retained_run.error == ""


def test_publication_database_error_keeps_successful_outcome(execution, register_step, monkeypatch, caplog):
    """Notification failure is logged without undoing a completed run or its body."""
    actor, _ = execution
    workflow = load_workflow(document("entry"), key="successful_publication_failure", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    step_run = system_queryset(StepRun).get(run=run)

    def invalid_write(*args, **kwargs):
        Workflow.objects.filter(pk=workflow.pk).update(key=None)

    register_step(RetainedBody)
    monkeypatch.setattr(runner_module, "publish_change", invalid_write)
    assert runner.execute(step_run.pk)
    assert system_queryset(Workflow).get(pk=workflow.pk).name == "Successful body retained"
    retained_run = system_queryset(WorkflowRun).get(pk=run.pk)
    assert retained_run.status == RunStatus.SUCCEEDED and retained_run.error == ""
    assert system_queryset(StepAttempt).get(step_run=step_run).result == "succeeded"
    assert any(record.exc_info and isinstance(record.exc_info[1], IntegrityError) for record in caplog.records)


def test_terminal_siblings_do_not_starve_later_tick_candidates(execution):
    """The tick excludes preserved terminal-run rows before its bounded batch."""

    actor, sent = execution
    siblings = [f"preserved_{index}" for index in range(101)]
    workflow = load_workflow({"nodes": {
        "entry": {"step": "echo", "next": {"done": ["failure", *siblings]}},
        "failure": {"step": "reject"},
        **{key: {"step": "echo"} for key in siblings},
    }}, key="terminal_batch", actor=actor)
    failed = WorkflowRun.objects.start(workflow, actor=actor)
    assert runner.execute(system_queryset(StepRun).get(run=failed, node_key="entry").pk)
    assert runner.execute(system_queryset(StepRun).get(run=failed, node_key="failure").pk)
    assert system_queryset(WorkflowRun).get(pk=failed.pk).status == RunStatus.FAILED
    retained = system_queryset(StepRun).filter(run=failed, status=StepRunStatus.READY)
    assert retained.count() == 101

    live_workflow = load_workflow(document("entry"), key="live_after_terminal_batch", actor=actor)
    live = WorkflowRun.objects.start(live_workflow, actor=actor)
    live_step = system_queryset(StepRun).get(run=live)
    with system_context(reason="age retained and active delivery timestamps"):
        StepRun.objects.filter(status=StepRunStatus.READY).update(dispatched_at=Now() - timedelta(seconds=61))
    sent.clear()

    assert runner.redispatch() == 1

    assert system_queryset(StepRun).get(pk=live_step.pk).dispatches == 1
    assert list(retained.values_list("dispatches", flat=True)) == [0] * 101
    assert [payload["kwargs"]["step_run_id"] for _name, payload in sent] == [live_step.pk]


@pytest.mark.parametrize("mode", [StepMode.DATABASE, StepMode.IO])
def test_settlement_recovery_losing_its_fence_keeps_the_run_recoverable(execution, register_step, monkeypatch, mode):
    """Failure recovery cannot turn an expired claim into a terminal run with a running row."""
    actor, _ = execution

    class ExpiredRecovery(RetainedBody):
        pass

    ExpiredRecovery.mode = mode
    register_step(ExpiredRecovery)
    workflow = load_workflow(document("entry"), key="expired_failure_recovery", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    step_run = system_queryset(StepRun).get(run=run)
    original_settle, original_record_failure = StepRunQuerySet.settle, runner_module._record_failure
    settlements = []

    def reject_result(self, row, settlement):
        settlements.append(type(settlement.settlement).__name__.lower())
        if isinstance(settlement.settlement, Done):
            raise DataError("Result persistence failed before its deadline elapsed")
        return original_settle(self, row, settlement)

    @contextmanager
    def expire_before_failure_recovery(operation):
        if operation == "settlement failure":
            StepRun.objects.filter(pk=step_run.pk).update(deadline_at=Now() - timedelta(seconds=1))
        with original_record_failure(operation):
            yield

    monkeypatch.setattr(StepRunQuerySet, "settle", reject_result)
    monkeypatch.setattr(runner_module, "_record_failure", expire_before_failure_recovery)

    assert runner.execute(step_run.pk) is False
    assert settlements == ["done", "fail"]
    retained_run = system_queryset(WorkflowRun).get(pk=run.pk)
    retained_step = system_queryset(StepRun).get(pk=step_run.pk)
    assert retained_run.status == RunStatus.RUNNING
    assert retained_run.finished_at is None and retained_run.error == ""
    attempts = system_queryset(StepAttempt).filter(step_run=step_run)
    if mode == StepMode.DATABASE:
        assert retained_step.status == StepRunStatus.READY and retained_step.attempt == 0
        assert not attempts.exists()
        assert system_queryset(Workflow).get(pk=workflow.pk).name != "Successful body retained"
    else:
        assert retained_step.status == StepRunStatus.RUNNING
        attempt = attempts.get()
        assert attempt.finished_at is None and attempt.result is None
        with system_context(reason="test elapsed deadline after IO failure recovery rollback"):
            StepRun.objects.filter(pk=step_run.pk).update(deadline_at=Now() - timedelta(seconds=1))
        assert runner.reap() == 1
        assert system_queryset(StepRun).get(pk=step_run.pk).status == StepRunStatus.FAILED
        assert attempts.get().result == "timed_out"
