"""Retention composes run-tree ownership and native protected evidence lifecycles."""

from datetime import timedelta

import pytest
from django.apps import apps
from django.db import models
from django.db.models.functions import Now

from angee.base.scoping import system_queryset
from angee.workflows.managers import PRUNE_BATCH_LIMIT
from angee.workflows.steps import Step
from angee.workflows.testing.drivers import load_workflow, run_until
from angee.workflows.testing.models import StepAttempt, StepRun, WorkflowRun
from tests.decisions_models import Decision, DecisionGroup
from tests.tables import model_tables
from tests.test_workflows_children import age_runs
from tests.test_workflows_children import child_graph as child_graph
from tests.test_workflows_review import answer, seats, start_review
from tests.test_workflows_review import review as review
from tests.workflow_steps import Value, document

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("workflow_step_classes")]


@pytest.fixture
def protected_execution(execution):
    """Register one temporary domain row with native protected evidence links."""

    class RetentionEvidence(models.Model):
        """Represent a domain record retaining an execution or an answered seat."""

        attempt = models.ForeignKey(StepAttempt, on_delete=models.PROTECT, null=True, related_name="+")
        decision = models.ForeignKey(Decision, on_delete=models.PROTECT, null=True, related_name="+")

        class Meta:
            """Keep the probe in an installed registry for Django's deletion collector."""

            app_label = "workflows"
            db_table = "test_workflows_retention_evidence"

    try:
        with model_tables((RetentionEvidence,)):
            yield RetentionEvidence
    finally:
        del apps.all_models["workflows"][RetentionEvidence._meta.model_name]
        apps.clear_cache()


def retry_prune(run):
    """Advance only the marked retry deadline, retaining the real lifecycle state."""
    system_queryset(WorkflowRun).filter(pk=run.pk).update(prune_after=Now() - timedelta(seconds=1))


def test_prune_deletes_owned_descendants_including_recent_grandchildren(child_graph, register_step):
    """One expired root owns the lifetime of its complete three-level tree."""
    actor, _, admitted, build = child_graph
    grand_workflow = load_workflow(document("entry"), key="retained-grandchild", actor=actor)
    grandchildren = []

    class StartGrandchild(Step[Value, Value, None]):
        """End without awaiting, exercising the owned-child cancellation policy."""

        key = "retention_start_grandchild"

        def run(self, ctx):
            grandchildren.append(ctx.start_run(grand_workflow, input=ctx.input.model_dump()))
            return ctx.done(ctx.input)

    register_step(StartGrandchild)
    parent, _ = build(child_step=StartGrandchild.key)
    run_until(parent)
    child = admitted[0]
    run_until(child)
    grandchild = system_queryset(WorkflowRun).get(pk=grandchildren[0].pk)
    assert grandchild.status == "canceled" and child.status == "succeeded"
    StepRun.objects.wake_runs(child.pk)
    run_until(parent)
    assert parent.status == "succeeded"
    age_runs(parent)
    run_ids = [parent.pk, child.pk, grandchild.pk]
    assert system_queryset(StepAttempt).filter(step_run__run_id__in=run_ids).exists()
    assert WorkflowRun.objects.prune() == 3
    assert not system_queryset(WorkflowRun).filter(pk__in=run_ids).exists()
    assert not system_queryset(StepRun).filter(run_id__in=run_ids).exists()
    assert not system_queryset(StepAttempt).filter(step_run__run_id__in=run_ids).exists()
    assert system_queryset(grand_workflow.versions.model).filter(workflow=grand_workflow).exists()


def test_prune_preserves_terminal_continuation_and_nulls_its_parent_step(child_graph):
    """A continuation's completed work survives deletion of its expired parent."""
    _, _, admitted, build = child_graph
    parent, _ = build(relation="continuation", await_child=False)
    run_until(parent)
    child = admitted[0]
    run_until(child)
    age_runs(parent)
    assert WorkflowRun.objects.prune() == 1
    child.refresh_from_db()
    assert child.status == "succeeded" and child.output == {"value": 7}
    assert child.parent_step_id is None and child.relation == "continuation"
    assert not system_queryset(WorkflowRun).filter(pk=parent.pk).exists()
    assert system_queryset(StepAttempt).filter(step_run__run=child).exists()


def test_active_continuation_marks_parent_until_a_later_retention_retry(child_graph):
    """The explicit retention rule defers deletion while a continuation is active."""
    _, _, admitted, build = child_graph
    parent, _ = build(relation="continuation", await_child=False)
    run_until(parent)
    child = admitted[0]
    age_runs(parent)
    assert WorkflowRun.objects.prune() == 0
    parent.refresh_from_db()
    child.refresh_from_db()
    assert parent.prune_after is not None and parent.prune_reason
    assert child.status == "running" and child.parent_step_id is not None
    assert not system_queryset(WorkflowRun).retention_candidates().filter(pk=parent.pk).exists()
    run_until(child)
    assert WorkflowRun.objects.prune() == 0
    retry_prune(parent)
    assert WorkflowRun.objects.prune() == 1
    child.refresh_from_db()
    assert child.parent_step_id is None and child.status == "succeeded"


def test_protected_attempt_rolls_back_the_whole_owned_tree_and_wait_links(child_graph, protected_execution):
    """A domain reference blocks deletion without losing a previously deleted child."""
    _, _, admitted, build = child_graph
    parent, _ = build()
    run_until(parent)
    child = admitted[0]
    run_until(child)
    StepRun.objects.wake_runs(child.pk)
    run_until(parent)
    waiter = system_queryset(StepRun).get(run=parent, node_key="await")
    assert waiter.awaited_run_id == child.pk
    attempt = system_queryset(StepAttempt).get(step_run__run=parent, step_run__node_key="start")
    evidence = protected_execution.objects.create(attempt=attempt)
    age_runs(parent)
    assert WorkflowRun.objects.prune() == 0
    parent.refresh_from_db()
    waiter.refresh_from_db()
    assert parent.prune_after is not None and parent.prune_reason
    assert waiter.awaited_run_id == child.pk
    assert system_queryset(WorkflowRun).filter(pk=child.pk).exists()
    assert system_queryset(StepAttempt).filter(pk=attempt.pk).exists()
    evidence.delete()
    retry_prune(parent)
    assert WorkflowRun.objects.prune() == 2


@pytest.mark.parametrize("protected", [False, True])
def test_prune_respects_decision_group_owner_across_every_reasked_round(review, protected_execution, protected):
    """Newest-first cleanup releases the protected chain only after every owner permits it."""
    _, people, _, _ = review
    run, step = start_review(review, input={"reject_rounds": 2})
    original = seats(step)[0]
    for _ in range(3):
        answer(seats(step)[0], people[0])
        run_until(run)
        step.refresh_from_db()
    assert run.status == "succeeded"
    groups = list(system_queryset(DecisionGroup).get(pk=step.decision_group_id).rounds())
    assert len(groups) == 3 and all(not group.is_deletable for group in groups)
    age_runs(run)
    if protected:
        evidence = protected_execution.objects.create(decision=original)
        assert WorkflowRun.objects.prune() == 0
        run.refresh_from_db()
        step.refresh_from_db()
        assert run.prune_after is not None and run.prune_reason
        assert step.decision_group_id == groups[0].pk
        assert system_queryset(DecisionGroup).filter(pk__in=[group.pk for group in groups]).count() == 3
        assert system_queryset(Decision).filter(pk=original.pk).exists()
        evidence.delete()
        retry_prune(run)
    assert WorkflowRun.objects.prune() == 1
    assert not system_queryset(WorkflowRun).filter(pk=run.pk).exists()
    assert not system_queryset(DecisionGroup).filter(pk__in=[group.pk for group in groups]).exists()
    assert not system_queryset(Decision).filter(pk=original.pk).exists()


def test_prune_checks_only_twenty_five_roots_and_marked_blockers_do_not_starve_later_batches(child_graph):
    """The bound includes blocked candidates; their marks admit later roots next tick."""
    actor, _, admitted, build = child_graph
    blocked, _ = build(relation="continuation", await_child=False)
    run_until(blocked)
    workflow = load_workflow(document("entry"), key="retention-batch", actor=actor)
    roots = []
    for _ in range(26):
        run = WorkflowRun.objects.start(workflow, actor=actor, input={"value": 3})
        run_until(run)
        roots.append(run)
    age_runs(blocked, *roots)
    assert PRUNE_BATCH_LIMIT == 25
    assert WorkflowRun.objects.prune() == 24
    blocked.refresh_from_db()
    assert blocked.prune_after is not None
    assert system_queryset(WorkflowRun).filter(pk__in=[run.pk for run in roots]).count() == 2
    assert WorkflowRun.objects.prune() == 2
    assert WorkflowRun.objects.prune() == 0
    assert system_queryset(WorkflowRun).filter(pk__in=[blocked.pk, admitted[0].pk]).count() == 2


def test_retention_setting_keeps_recent_terminal_runs(execution, settings):
    """The configured age threshold applies before destructive cleanup begins."""
    actor, _ = execution
    settings.ANGEE_WORKFLOW_RETENTION_DAYS = 120
    workflow = load_workflow(document("entry"), actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor, input={"value": 3})
    run_until(run)
    age_runs(run)
    assert WorkflowRun.objects.prune() == 0
    settings.ANGEE_WORKFLOW_RETENTION_DAYS = 90
    assert WorkflowRun.objects.prune() == 1
